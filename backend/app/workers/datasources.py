"""数据源导入 Worker 入口。

运行方式::

    python -m app.workers.datasources

消费两类 outbox 事件，每条事件在独立事务中完成：

1. ``datasource.import_requested``：抢占行 → 下载预览视频 → 校验 → 存对象存储 →
   建媒体资产 → 推进导入状态 → 标记源事件 PUBLISHED。
2. ``datasource.import.succeed``（投递类）：把该次导入的媒体资产按
   ``episode_key`` 物化成 Canonical Episode 并尝试自动就绪（V31-014）。
   此前这步要靠客户端再发一次手工请求，链路一断就停在 RawAsset 阶段。

Worker 与 HTTP API 使用同一镜像但独立进程；**HTTP 线程不下载大文件**。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.application.datasources.events import DATASOURCE_IMPORT_ACTION_EVENTS
from app.application.datasources.import_runner import ImportRunner
from app.application.ingestion.ports import ClaimedValidationEvent
from app.composition.datasources import build_episode_materializer
from app.core.config import Settings, get_settings
from app.core.logging import bind_trace, configure_logging, trace_from_event_payload
from app.domain.datasources.errors import DataSourceDomainError
from app.domain.episodes.errors import EpisodeDomainError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.collection_audit import SqlOutboxRepository
from app.infrastructure.repositories.dataset_audit import SqlDatasetAuditRepository
from app.infrastructure.repositories.datasources import (
    SqlDataSourceImportLogRepository,
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.infrastructure.storage.data_source_objects import DataSourceObjectWriter
from app.infrastructure.storage.s3 import S3MultipartStorage

logger = logging.getLogger("ddp.worker.datasources")

IMPORT_REQUESTED_EVENT = "datasource.import_requested"
# 消费 ``datasource.import.succeed``。事件名只在
# ``application/datasources/events.py`` 定义一次（R-13 字面量映射），这里
# 通过常量引用；注释里的字面量供 ``scripts/check_outbox_events.py`` 校验
# 消费者声明。
EPISODES_MATERIALIZE_EVENT = DATASOURCE_IMPORT_ACTION_EVENTS["succeed"]
_HTTP_TIMEOUT_SECONDS = 120.0


class ImportProcessingStatus(StrEnum):
    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRIED = "RETRIED"
    DEAD_LETTER = "DEAD_LETTER"


class _HttpxFetcher:
    """流式 HTTP 下载 + 内容签名校验（委托给 data-platform 的 importer）。"""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __call__(self, *, repo_id: str, revision: str, path: str):
        from ddp_data.huggingface.importer import fetch_file

        return fetch_file(self, repo_id=repo_id, revision=revision, path=path)

    def stream(self, url: str) -> Iterator[bytes]:
        """满足 data-platform 的 ``HttpFetcher`` 协议：按块产出响应体。"""
        with self._client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            yield from response.iter_bytes()


def _registry_parser(rows: list[dict[str, object]]) -> tuple[object, ...]:
    """把已解析的 JSONL 行交给 Adapter 注册表选出解析器并执行。

    ``ImportRunner`` 的 ``parse_episodes`` 契约是「已解析的行 → Episode 序列」，
    而 Adapter 的 Parser 契约是 bytes。这里把行重新编码回 JSONL 再交给注册表，
    让「选哪个 Adapter」由注册表按内容探测决定——探测逻辑只需要一处。
    """
    import json

    from app.application.datasources.adapter_resolver import resolve_and_parse

    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows).encode()
    return resolve_and_parse(payload).episodes


def _parse_metadata(data: bytes) -> list[dict[str, object]]:
    """把 metadata.jsonl 字节解析为行列表；格式错误抛异常由调用方落库。

    实现已下沉到 ``ddp_data.adapters.builtin.parse_jsonl_rows``：Adapter 的
    Parser 契约是 bytes，解码逻辑必须有单一归属，否则换个入口就得再写一份。
    这里仅保留薄委托，避免既有调用点与测试大改。
    """
    from ddp_data.adapters.builtin import GenericJsonlParseError, parse_jsonl_rows

    try:
        return parse_jsonl_rows(data)
    except GenericJsonlParseError as exc:
        # 调用方按 ValueError 处理（导入失败留痕），保持既有错误契约不变。
        raise ValueError(f"metadata 解析失败：{exc}") from exc


def build_runner(session: Session, settings: Settings) -> ImportRunner:
    return ImportRunner(
        sources=SqlDataSourceRepository(session),
        imports=SqlDataSourceImportRepository(session),
        assets=SqlMediaAssetRepository(session),
        raw_assets=SqlRawAssetRepository(session),
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlOutboxRepository(session),
        storage=DataSourceObjectWriter(S3MultipartStorage(settings)),
        uow=session,
        logs=SqlDataSourceImportLogRepository(session),
    )


class DataSourceImportWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        client: httpx.Client,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._client = client

    def process_once(self, *, now: datetime | None = None) -> dict[str, object]:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlOutboxValidationQueue(session)
            claimed = queue.claim_next(now=moment, event_type=IMPORT_REQUESTED_EVENT)
            handler = self._process_claimed
            if claimed is None:
                # 导入成功后由本 Worker 自动物化 Episode（V31-014）。共用同一
                # 进程是因为两类事件共享适配器与重试策略；先后顺序由 Outbox
                # 保证——成功事件只有在导入事务提交后才可见。
                claimed = queue.claim_next(now=moment, event_type=EPISODES_MATERIALIZE_EVENT)
                handler = self._process_episodes
            if claimed is None:
                return {"status": ImportProcessingStatus.IDLE.value}
            # 追踪上下文覆盖处理与结果日志；来源是事件 payload 的 request_id，
            # 缺失时生成并标注 generated，退出 with 后复位。
            with bind_trace(trace_from_event_payload(claimed.payload)) as trace:
                result = handler(session, queue, claimed, moment)
                _log_result(result)
                if trace.is_generated:
                    logger.info(
                        "导入事件缺少可用 request_id，已生成本地 trace_id event_id=%s",
                        claimed.event_id,
                    )
                return result

    def _process_claimed(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        claimed: ClaimedValidationEvent,
        moment: datetime,
    ) -> dict[str, object]:
        raw_id = claimed.payload.get("import_id")
        try:
            import_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"payload 缺少合法 import_id：{raw_id!r}",
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.DEAD_LETTER.value,
                "event_id": str(claimed.event_id),
            }

        # 系统执行身份必须是一个**真实存在**的 user：
        # ``data_source_imports.updated_by`` / ``raw_assets.created_by`` 都
        # 有 FK 到 ``users``。此前用 ``project_id`` 当 actor，任何一次状态
        # 推进都会外键失败（与 ingestion_jobs Worker 修复前的缺陷同源：
        # 表现为"任务反复重试却永不成功"）。取导入任务的创建人，语义也
        # 成立——系统是代发起人执行其导入。
        actor_id = _import_creator(session, import_id)
        if actor_id is None:
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"导入任务不存在或缺少创建人：{import_id}",
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.DEAD_LETTER.value,
                "event_id": str(claimed.event_id),
                "import_id": str(import_id),
            }
        scope = ProjectScope(claimed.project_id, actor_id=actor_id)

        try:
            runner = build_runner(session, self._settings)
            outcome = runner.execute(
                scope,
                import_id,
                fetch=_HttpxFetcher(self._client),
                parse_metadata=_parse_metadata,
                # 解析器由 **Adapter 注册表** 决定，而不是在这里硬编码某个
                # 具体数据集的名字。此前写死 parse_preview_metadata，
                # 换一种数据格式就必须改 Worker，注册表等于形同虚设。
                parse_episodes=_registry_parser,
                now=moment,
            )
            queue.mark_published(claimed.event_id, now=moment)
            session.commit()
            return {
                "status": ImportProcessingStatus.PUBLISHED.value,
                "import_id": str(import_id),
                "episodes": outcome.episode_count,
                "assets": outcome.asset_count,
                "bytes": outcome.total_bytes,
            }
        except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
            session.rollback()
            # 幂等优先：任务已在终态（SUCCEEDED/CANCELLED）说明这条导入
            # 早就被处理过，事件是重复投递。此时**必须直接 PUBLISHED**，
            # 不能走重试——重试永远只会得到同一句「状态不允许执行」，
            # 白烧 attempts 直到死信，把"已完成的导入"记成"反复失败"。
            # 真实库里 38 条历史事件正是这个形态。
            if _import_already_terminal(session, import_id):
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return {
                    "status": ImportProcessingStatus.PUBLISHED.value,
                    "import_id": str(import_id),
                    "skipped": "导入任务已在终态，事件为重复投递",
                }
            # **先判死信，再决定是否退回 PENDING**。顺序反了会留下
            # 「导入 PENDING、事件已死信」的永久孤儿：导入侧
            # ``max_attempts``（默认 3）与事件侧
            # ``validation_max_attempts``（默认 5、可配）不同源，后者更小
            # 时最后一次失败会先退回 PENDING，再把事件判死信——没有事件、
            # 没有终态、也没有客户端重试入口。死信时保持 FAILED，错误摘要
            # 与步骤日志都留在导入上，人工可查。
            attempts = claimed.attempts + 1
            if attempts >= self._settings.validation_max_attempts:
                queue.mark_dead_letter(
                    claimed.event_id,
                    attempts=attempts,
                    last_error=str(exc)[:2000],
                )
                session.commit()
                return {
                    "status": ImportProcessingStatus.DEAD_LETTER.value,
                    "import_id": str(import_id),
                    "attempts": attempts,
                    "released_for_retry": False,
                    "error": str(exc)[:500],
                }
            # 还有重试机会：把**可重试**的失败从 FAILED 退回 PENDING。
            #
            # 不这么做的话：本次已把任务写成 FAILED，而下面又会
            # mark_retry 让事件下一轮再来；下一轮 execute 要求 PENDING，
            # 状态机也拒绝 FAILED->RUNNING，于是每次重试都必然再失败，
            # 直到死信——一次网络抖动就永久丢掉这条导入。
            released = False
            try:
                # 回滚后重新解析身份：不能用上面的局部变量，事务内读到的
                # 值已随回滚失效。
                retry_actor = _import_creator(session, import_id)
                runner = build_runner(session, self._settings)
                if retry_actor is not None:
                    released = runner.release_for_retry(
                        ProjectScope(claimed.project_id, actor_id=retry_actor),
                        import_id,
                        moment=moment,
                    )
            except Exception:  # noqa: BLE001 - 退回失败不得掩盖原始异常
                session.rollback()
                logger.exception("导入任务退回重试失败", extra={"import_id": str(import_id)})
            delay = min(
                self._settings.validation_retry_max_seconds,
                self._settings.validation_retry_base_seconds * (2 ** (attempts - 1)),
            )
            queue.mark_retry(
                claimed.event_id,
                attempts=attempts,
                available_at=moment + timedelta(seconds=delay),
                last_error=str(exc)[:2000],
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.RETRIED.value,
                "import_id": str(import_id),
                "attempts": attempts,
                "released_for_retry": released,
                "error": str(exc)[:500],
            }

    def _process_episodes(
        self,
        session: Session,
        queue: SqlOutboxValidationQueue,
        claimed: ClaimedValidationEvent,
        moment: datetime,
    ) -> dict[str, object]:
        """导入成功事件 → Canonical Episode（含自动就绪）。

        幂等由 ``ImportEpisodeMaterializer`` 保证：同一 ``episode_key`` 用确定性
        code 判重，重复投递只记为 skipped，不会造出第二份 Episode。

        领域异常（导入不存在 / 未成功 / 没有资产、Episode 校验失败）是永久性
        结论：同样的输入重试只会得到同样的拒绝，因此直接死信并留下原因。
        其余异常（数据库/对象存储/网络抖动）按指数退避重试，超过上限才死信。

        事务边界：``EpisodeService.create`` 每条 Episode 各自提交，因此中途
        永久失败时已创建的 Episode 会保留。重放是安全的——确定性 code 会让
        已存在的记为 skipped，只继续处理剩下的，不需要额外的补偿逻辑。
        """
        raw_id = claimed.payload.get("import_id")
        try:
            import_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"payload 缺少合法 import_id：{raw_id!r}",
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.DEAD_LETTER.value,
                "event": "episodes",
                "event_id": str(claimed.event_id),
            }

        # 与导入事件同口径：Worker 以导入发起人的名义执行，满足审计列的
        # users 外键约束，也留下"谁的数据被物化"的语义。
        actor_id = _import_creator(session, import_id)
        if actor_id is None:
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=f"导入任务不存在或缺少创建人：{import_id}",
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.DEAD_LETTER.value,
                "event": "episodes",
                "import_id": str(import_id),
            }
        scope = ProjectScope(claimed.project_id, actor_id=actor_id)

        try:
            result = build_episode_materializer(session).materialize(scope, import_id)
            queue.mark_published(claimed.event_id, now=moment)
            session.commit()
            return {
                "status": ImportProcessingStatus.PUBLISHED.value,
                "event": "episodes",
                "import_id": str(import_id),
                "created": result.episode_count,
                "skipped": len(result.skipped_episode_keys),
                "ready": result.ready_count,
                "blocked": result.blocked_count,
            }
        except (DataSourceDomainError, EpisodeDomainError) as exc:
            session.rollback()
            queue.mark_dead_letter(
                claimed.event_id,
                attempts=claimed.attempts + 1,
                last_error=str(exc)[:2000],
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.DEAD_LETTER.value,
                "event": "episodes",
                "event_id": str(claimed.event_id),
                "import_id": str(import_id),
                "error": str(exc)[:500],
            }
        except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
            session.rollback()
            attempts = claimed.attempts + 1
            if attempts >= self._settings.validation_max_attempts:
                queue.mark_dead_letter(
                    claimed.event_id,
                    attempts=attempts,
                    last_error=str(exc)[:2000],
                )
                session.commit()
                return {
                    "status": ImportProcessingStatus.DEAD_LETTER.value,
                    "event": "episodes",
                    "import_id": str(import_id),
                    "attempts": attempts,
                    "error": str(exc)[:500],
                }
            delay = min(
                self._settings.validation_retry_max_seconds,
                self._settings.validation_retry_base_seconds * (2 ** (attempts - 1)),
            )
            queue.mark_retry(
                claimed.event_id,
                attempts=attempts,
                available_at=moment + timedelta(seconds=delay),
                last_error=str(exc)[:2000],
            )
            session.commit()
            return {
                "status": ImportProcessingStatus.RETRIED.value,
                "event": "episodes",
                "import_id": str(import_id),
                "attempts": attempts,
                "error": str(exc)[:500],
            }

    def run(self, *, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            result = self.process_once()
            if result["status"] == ImportProcessingStatus.IDLE.value:
                stop_event.wait(self._settings.validation_worker_poll_interval_seconds)


def _log_result(result: dict[str, object]) -> None:
    """在已绑定 trace 的上下文内记录事件结果，便于按 ID 串联查询。"""
    status = result.get("status")
    if result.get("event") == "episodes":
        if status == ImportProcessingStatus.PUBLISHED.value:
            logger.info(
                "导入产物已自动物化 import_id=%s created=%s skipped=%s ready=%s blocked=%s",
                result.get("import_id"),
                result.get("created"),
                result.get("skipped"),
                result.get("ready"),
                result.get("blocked"),
            )
        elif status == ImportProcessingStatus.RETRIED.value:
            logger.warning(
                "Episode 物化将重试 import_id=%s attempts=%s error=%s",
                result.get("import_id"),
                result.get("attempts"),
                result.get("error"),
            )
        elif status == ImportProcessingStatus.DEAD_LETTER.value:
            logger.error(
                "Episode 物化进入死信 event_id=%s import_id=%s error=%s",
                result.get("event_id"),
                result.get("import_id"),
                result.get("error"),
            )
        return
    if status == ImportProcessingStatus.PUBLISHED.value:
        logger.info(
            "导入事件已处理 import_id=%s episodes=%s assets=%s",
            result.get("import_id"),
            result.get("episodes"),
            result.get("assets"),
        )
    elif status == ImportProcessingStatus.RETRIED.value:
        logger.warning(
            "导入事件将重试 import_id=%s attempts=%s error=%s",
            result.get("import_id"),
            result.get("attempts"),
            result.get("error"),
        )
    elif status == ImportProcessingStatus.DEAD_LETTER.value:
        logger.error(
            "导入事件进入死信 event_id=%s import_id=%s error=%s",
            result.get("event_id"),
            result.get("import_id"),
            result.get("error"),
        )


def _import_creator(session: Session, import_id: UUID) -> UUID | None:
    """导入任务的创建人，用作 Worker 的系统执行身份。

    只暴露一个标量查询，不把 ``created_by`` 加进领域模型——Worker 需要的是
    "以谁的名义写审计列"，不是业务字段。
    """
    from sqlalchemy import select

    from app.infrastructure.db.models.datasources import DataSourceImportModel

    return session.scalar(
        select(DataSourceImportModel.created_by).where(DataSourceImportModel.id == import_id)
    )


def _import_already_terminal(session: Session, import_id: UUID) -> bool:
    """任务是否已不再接受执行（重复投递，应直接 PUBLISHED）。

    项目范围由事件本身保证。``SUCCEEDED`` / ``CANCELLED`` 是绝对终态。
    ``FAILED`` 要分开看：**首次/可重试失败必须交给 release_for_retry 退回
    PENDING**，否则 0034 的重试机制永远不会生效——``_fail`` 刚把任务写成
    FAILED，这里若直接当成"重复投递"，一次网络抖动就会让导入永久卡死。
    只有 ``attempt >= max_attempts``（确实不再重试）才按终态处理。
    """
    from sqlalchemy import select

    from app.infrastructure.db.models.datasources import DataSourceImportModel

    row = session.execute(
        select(
            DataSourceImportModel.status,
            DataSourceImportModel.attempt,
            DataSourceImportModel.max_attempts,
        ).where(DataSourceImportModel.id == import_id)
    ).one_or_none()
    if row is None:
        return False
    status, attempt, max_attempts = row
    if status in {"SUCCEEDED", "CANCELLED"}:
        return True
    return status == "FAILED" and attempt >= max_attempts


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 数据源导入 Worker")
    parser.add_argument("--once", action="store_true", help="处理一次后退出（用于测试）")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging()
    factory = create_session_factory(get_engine())
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        worker = DataSourceImportWorker(factory, settings, client)
        if args.once:
            result = worker.process_once()
            logger.info("单次处理完成", extra={"result": result})
            return 0
        # 启动日志不是装饰：容器 Up 但「进程到底在不在消费」是运维最先要确认
        # 的事。此前这个 Worker 与 ingestion-job-worker 都没有任何启动输出，
        # 只能靠 `docker top` 猜。
        logger.info(
            "数据源导入 Worker 已启动",
            extra={"poll_interval": settings.validation_worker_poll_interval_seconds},
        )
        worker.run(stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
