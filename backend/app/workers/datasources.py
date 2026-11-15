"""数据源导入 Worker 入口。

运行方式::

    python -m app.workers.datasources

消费 outbox 中的 ``datasource.import_requested`` 事件。每个事件在独立事务中完成：
抢占行 → 下载预览视频 → 校验 → 存对象存储 → 建媒体资产 → 推进导入状态 →
标记源事件 PUBLISHED。

Worker 与 HTTP API 使用同一镜像但独立进程；**HTTP 线程不下载大文件**。
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.application.datasources.import_runner import ImportRunner
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.collection_audit import SqlOutboxRepository
from app.infrastructure.repositories.dataset_audit import SqlDatasetAuditRepository
from app.infrastructure.repositories.datasources import (
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


def _parse_metadata(data: bytes) -> list[dict[str, object]]:
    """把 metadata.jsonl 字节解析为行列表；格式错误抛异常由调用方落库。"""
    rows: list[dict[str, object]] = []
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"metadata 第 {lineno} 行不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"metadata 第 {lineno} 行不是对象")
        rows.append(value)
    if not rows:
        raise ValueError("metadata 为空")
    return rows


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
            if claimed is None:
                return {"status": ImportProcessingStatus.IDLE.value}

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

            try:
                runner = build_runner(session, self._settings)
                # Worker 用事件里的 project_id 构造作用域；actor 取项目自身，
                # 因为这是系统行为而非某个用户的请求。
                scope = ProjectScope(claimed.project_id, actor_id=claimed.project_id)
                from ddp_data.lerobot import parse_preview_metadata

                outcome = runner.execute(
                    scope,
                    import_id,
                    fetch=_HttpxFetcher(self._client),
                    parse_metadata=_parse_metadata,
                    parse_episodes=parse_preview_metadata,
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
                    "import_id": str(import_id),
                    "attempts": attempts,
                    "error": str(exc)[:500],
                }

    def run(self, *, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            result = self.process_once()
            if result["status"] == ImportProcessingStatus.IDLE.value:
                stop_event.wait(self._settings.validation_worker_poll_interval_seconds)


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
        worker.run(stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
