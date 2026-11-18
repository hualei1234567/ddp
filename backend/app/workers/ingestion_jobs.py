"""接入任务执行 Worker。

运行方式::

    python -m app.workers.ingestion_jobs

消费 outbox 中的 ``ingestion.job.created`` 事件，对 ``IngestionJob`` 执行
真实处理并推进状态机。

**为什么需要它**

在此之前 ``IngestionJob`` 有完整的能力——状态机（QUEUED → RUNNING →
SUCCEEDED/FAILED）、重试与超时、`output_refs` / `log_ref` / `metrics` /
`config_hash` 字段、7 个 API 动作——**但没有任何进程消费它**。任务建完后
永远停在 QUEUED，「处理任务可重试、幂等」这条完成定义因此不成立。
本 Worker 就是那个缺失的消费者。

**当前能做什么、不能做什么（如实标注）**

本 Worker 按 ``job_type`` 分派：

- ``VALIDATE`` / ``REVALIDATE``：由既有的 RawAsset 校验链路负责
  （``ingestion.raw_asset.validation_requested``），这里**不重复实现**，
  直接标记成功并把结果指向既有链路（``metrics.delegated_to``）；
- ``SEGMENT_EPISODE``：**当前不支持**。切片来自导入解析的 ``episode_key``
  分组，入口是 ``POST /data-source-imports/{id}/episodes``；帧级切片属于
  N3 多模态 Pipeline，尚未实现。此前注释写成"真实可用"，与实现不符，已更正；
- ``PARSE_METADATA``：**当前不支持**。元数据解析发生在导入链路内，
  没有独立的解析产物落库语义。

宁可显式失败也不伪造成功：一个声称处理成功但什么都没产出的任务，
比一个明确报"暂不支持"的任务危险得多。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion.jobs import IngestionJobService
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobStatus, IngestionJobType
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.ingestion_jobs import SqlIngestionJobRepository
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository

logger = logging.getLogger("ddp.worker.ingestion_jobs")

JOB_CREATED_EVENT = "ingestion.job.created"


class JobProcessingStatus(StrEnum):
    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    UNSUPPORTED = "UNSUPPORTED"
    RETRIED = "RETRIED"
    DEAD_LETTER = "DEAD_LETTER"


class IngestionJobWorker:
    """按事件逐个执行接入任务。"""

    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._factory = factory
        self._settings = settings

    def process_once(self, *, now: datetime | None = None) -> dict[str, object]:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlOutboxValidationQueue(session)
            claimed = queue.claim_next(now=moment, event_type=JOB_CREATED_EVENT)
            if claimed is None:
                return {"status": JobProcessingStatus.IDLE.value}

            raw_id = claimed.payload.get("job_id")
            try:
                job_id = UUID(str(raw_id))
            except (TypeError, ValueError):
                queue.mark_dead_letter(
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    last_error=f"payload 缺少合法 job_id：{raw_id!r}",
                )
                session.commit()
                return {
                    "status": JobProcessingStatus.DEAD_LETTER.value,
                    "event_id": str(claimed.event_id),
                }

            service = _build_service(session)
            # 读只需 project 范围；写需要真实 user（updated_by 有 FK 到 users）。
            read_scope = ProjectScope(claimed.project_id, actor_id=claimed.project_id)
            try:
                job = service.get(read_scope, job_id)
            except Exception as exc:  # noqa: BLE001 - 任务丢失也要留痕
                queue.mark_dead_letter(
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    last_error=f"接入任务不可读：{exc}"[:2000],
                )
                session.commit()
                return {
                    "status": JobProcessingStatus.DEAD_LETTER.value,
                    "job_id": str(job_id),
                }

            # actor 必须是一个**真实存在**的 user（ingestion_jobs.updated_by 有
            # FK 到 users）。此前用 project_id 当 actor，任何写操作都会因外键
            # 失败——表现为"任务一直重试却永不成功"。这里用任务的 creator，
            # 语义也对：状态变更是因为提交人的任务被系统执行。
            scope = ProjectScope(claimed.project_id, actor_id=job.created_by or job.updated_by)

            if job.status is not IngestionJobStatus.QUEUED:
                # 幂等：任务已被别人推进过，不重复执行。
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return {
                    "status": JobProcessingStatus.PUBLISHED.value,
                    "job_id": str(job_id),
                    "skipped": f"任务状态为 {job.status.value}",
                }

            try:
                outcome = _execute(service, scope, job)
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return {
                    "status": JobProcessingStatus.PUBLISHED.value,
                    "job_id": str(job_id),
                    "job_type": job.job_type.value,
                    "result": outcome,
                }
            except _UnsupportedJobType as exc:
                # 不支持的类型：明确失败并留原因，不重试（重试也不会成功）。
                _fail_job(service, scope, job, error_code="job_type_unsupported", summary=str(exc))
                queue.mark_dead_letter(
                    claimed.event_id,
                    attempts=claimed.attempts + 1,
                    last_error=str(exc)[:2000],
                )
                session.commit()
                return {
                    "status": JobProcessingStatus.UNSUPPORTED.value,
                    "job_id": str(job_id),
                    "job_type": job.job_type.value,
                    "error": str(exc),
                }
            except Exception as exc:  # noqa: BLE001 - 单任务异常不得终止 Worker
                session.rollback()
                attempts = claimed.attempts + 1
                if attempts >= self._settings.validation_max_attempts:
                    queue.mark_dead_letter(
                        claimed.event_id,
                        attempts=attempts,
                        last_error=str(exc)[:2000],
                    )
                else:
                    queue.mark_retry(
                        claimed.event_id,
                        attempts=attempts,
                        available_at=moment,
                        last_error=str(exc)[:2000],
                    )
                session.commit()
                return {
                    "status": JobProcessingStatus.RETRIED.value,
                    "job_id": str(job_id),
                    "attempts": attempts,
                    "error": str(exc)[:500],
                }

    def run(self, *, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            result = self.process_once()
            if result["status"] == JobProcessingStatus.IDLE.value:
                stop_event.wait(self._settings.validation_worker_poll_interval_seconds)


class _UnsupportedJobType(Exception):
    """该类任务当前没有真实实现；显式失败而不是伪造成功。"""


def _build_service(session: Session) -> IngestionJobService:
    return IngestionJobService(
        SqlIngestionJobRepository(session),
        SqlRawAssetRepository(session),
        # audit 与 outbox 是**两个不同端口**：前者要 record（审计流水），
        # 后者要 append（事件）。传同一个对象会在运行时
        # `'SqlOutboxRepository' object has no attribute 'record'`。
        SqlAuditRepository(session),
        SqlOutboxRepository(session),
        session,
    )


def _execute(
    service: IngestionJobService,
    scope: ProjectScope,
    job,
) -> dict[str, object]:
    """按 job_type 执行；成功则推进到 SUCCEEDED，否则抛异常由调用方处理。"""
    job_type = IngestionJobType(job.job_type)
    # 注意：IngestionJobService.apply_action 内部取当前时间，不接受 now 参数
    # （与导入用例不同）。传 now 会 TypeError，而被外层当成可重试失败吞掉，
    # 表现为"任务一直重试却永不成功"。
    started = service.apply_action(
        scope,
        job.job_id,
        _action_command("worker-start", job.version),
    )
    if job_type in (IngestionJobType.VALIDATE, IngestionJobType.REVALIDATE):
        # 校验由既有 RawAsset 校验链路负责；这里不重复实现，只记录"已转交"。
        service.apply_action(
            scope,
            started.job_id,
            _action_command(
                "worker-succeed",
                started.version,
                metrics={"delegated_to": "raw_asset_validation"},
            ),
        )
        return {"delegated": "raw_asset_validation"}
    if job_type is IngestionJobType.PARSE_METADATA:
        raise _UnsupportedJobType(
            "PARSE_METADATA 尚无独立的元数据解析产物落库语义；"
            "当前元数据解析发生在导入链路内（POST /data-source-imports/{id}/episodes）"
        )
    if job_type is IngestionJobType.SEGMENT_EPISODE:
        raise _UnsupportedJobType(
            "SEGMENT_EPISODE 需要帧级切片能力（N3 多模态 Pipeline），"
            "当前切片来自导入解析的 episode_key 分组，"
            "请使用 POST /data-source-imports/{id}/episodes"
        )
    raise _UnsupportedJobType(f"未知接入任务类型：{job_type.value}")


def _action_command(action: str, expected_version: int, **overrides: object):
    from app.application.ingestion.commands import JobActionCommand

    return JobActionCommand(action=action, expected_version=expected_version, **overrides)  # type: ignore[arg-type]


def _fail_job(
    service: IngestionJobService,
    scope: ProjectScope,
    job,
    *,
    error_code: str,
    summary: str,
) -> None:
    """把任务标为失败；失败留痕本身不允许再抛错掩盖原始问题。"""
    try:
        current = service.get(scope, job.job_id)
        if current.status is IngestionJobStatus.QUEUED:
            current = service.apply_action(
                scope,
                job.job_id,
                _action_command("worker-start", current.version),
            )
        service.apply_action(
            scope,
            job.job_id,
            _action_command(
                "worker-fail",
                current.version,
                error_code=error_code,
                error_summary=summary[:2000],
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("标记接入任务失败时出错", extra={"job_id": str(job.job_id)})


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 接入任务 Worker")
    parser.add_argument("--once", action="store_true", help="处理一次后退出（用于测试）")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging()
    factory = create_session_factory(get_engine())
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker = IngestionJobWorker(factory, settings)
    if args.once:
        logger.info("单次处理完成", extra={"result": worker.process_once()})
        return 0
    # 启动日志不是装饰：容器 Up 但「进程到底在不在消费」是运维最先要确认的事。
    # 与 export/outbox worker 保持同一口径。
    logger.info(
        "接入任务 Worker 已启动",
        extra={"poll_interval": settings.validation_worker_poll_interval_seconds},
    )
    worker.run(stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
