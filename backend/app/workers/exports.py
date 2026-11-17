"""ExportJob 异步导出 Worker。

运行方式::

    python -m app.workers.exports

消费 outbox 中的 ``dataset.export.requested`` 事件。每个事件在独立事务中完成：
抢占 → 启动导出 → 写对象存储 → 回读校验 → 记录状态/审计/outbox → 标记事件
PUBLISHED。失败与部分失败均落库；显式重试只能经 HTTP 动作触发。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.application.datasets.export_execution import ExportExecutionService
from app.application.datasets.export_ports import ExportObjectStorage
from app.application.datasets.exports import EXPORT_REQUESTED_EVENT
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.dataset_repositories import (
    SqlDatasetItemRepository,
    SqlDatasetRepository,
    SqlDatasetVersionRepository,
)
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository
from app.infrastructure.repositories.export_queue import SqlExportQueue
from app.infrastructure.repositories.export_sources import SqlExportEpisodeSourceDirectory
from app.infrastructure.storage.s3 import S3MultipartStorage

logger = logging.getLogger("ddp.worker.exports")


class ExportProcessingStatus(StrEnum):
    IDLE = "IDLE"
    PUBLISHED = "PUBLISHED"
    RETRIED = "RETRIED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True, slots=True)
class ExportProcessResult:
    status: ExportProcessingStatus
    event_id: UUID | None = None
    job_id: UUID | None = None
    attempts: int = 0
    error: str | None = None


def build_execution_service(
    session: Session,
    settings: Settings,
    storage: ExportObjectStorage,
) -> ExportExecutionService:
    return ExportExecutionService(
        datasets=SqlDatasetRepository(session),
        versions=SqlDatasetVersionRepository(session),
        items=SqlDatasetItemRepository(session),
        jobs=SqlExportJobRepository(session),
        storage=storage,
        episode_sources=SqlExportEpisodeSourceDirectory(session),
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        uow=session,
        artifact_chunk_size_bytes=settings.export_artifact_chunk_size_bytes,
    )


class ExportWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        storage: ExportObjectStorage,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._storage = storage

    def process_once(self, *, now: datetime | None = None) -> ExportProcessResult:
        moment = now or datetime.now(UTC)
        with self._factory() as session:
            queue = SqlExportQueue(session)
            claimed = queue.claim_next(now=moment, event_type=EXPORT_REQUESTED_EVENT)
            if claimed is None:
                return ExportProcessResult(status=ExportProcessingStatus.IDLE)
            job_id = _job_id(claimed.payload, claimed.aggregate_id)
            try:
                jobs = SqlExportJobRepository(session)
                lookup_scope = ProjectScope(claimed.project_id, actor_id=claimed.project_id)
                job = jobs.get(lookup_scope, job_id)
                if job is None:
                    queue.mark_published(claimed.event_id, now=moment)
                    session.commit()
                    return ExportProcessResult(
                        status=ExportProcessingStatus.PUBLISHED,
                        event_id=claimed.event_id,
                        job_id=job_id,
                    )
                scope = ProjectScope(claimed.project_id, actor_id=job.requested_by)
                service = build_execution_service(session, self._settings, self._storage)
                service.execute(scope, job_id, now=moment)
                queue.mark_published(claimed.event_id, now=moment)
                session.commit()
                return ExportProcessResult(
                    status=ExportProcessingStatus.PUBLISHED,
                    event_id=claimed.event_id,
                    job_id=job_id,
                )
            except Exception as exc:  # noqa: BLE001 - 单事件异常不得终止 Worker
                session.rollback()
                attempts = claimed.attempts + 1
                if attempts >= self._settings.export_worker_max_attempts:
                    queue.mark_dead_letter(
                        claimed.event_id,
                        attempts=attempts,
                        last_error=str(exc)[:2000],
                    )
                    session.commit()
                    return ExportProcessResult(
                        status=ExportProcessingStatus.DEAD_LETTER,
                        event_id=claimed.event_id,
                        job_id=job_id,
                        attempts=attempts,
                        error=str(exc),
                    )
                delay = _retry_delay(
                    attempts,
                    base_seconds=self._settings.export_worker_retry_base_seconds,
                    max_seconds=self._settings.export_worker_retry_max_seconds,
                )
                queue.mark_retry(
                    claimed.event_id,
                    attempts=attempts,
                    available_at=moment + delay,
                    last_error=str(exc)[:2000],
                )
                session.commit()
                return ExportProcessResult(
                    status=ExportProcessingStatus.RETRIED,
                    event_id=claimed.event_id,
                    job_id=job_id,
                    attempts=attempts,
                    error=str(exc),
                )

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        once: bool = False,
        drain: bool = False,
    ) -> None:
        stop = stop_event or threading.Event()
        logger.info(
            "Export Worker 已启动 poll_interval=%s max_event_attempts=%s",
            self._settings.export_worker_poll_interval_seconds,
            self._settings.export_worker_max_attempts,
        )
        while not stop.is_set():
            try:
                result = self.process_once()
            except Exception:  # noqa: BLE001 - 单轮异常不得终止 Worker
                logger.exception("处理导出事件时发生未预期异常，将在下一轮重试")
                if once or drain:
                    return
                stop.wait(self._settings.export_worker_poll_interval_seconds)
                continue
            if once:
                return
            if drain and result.status is ExportProcessingStatus.IDLE:
                return
            if result.status is ExportProcessingStatus.IDLE:
                stop.wait(self._settings.export_worker_poll_interval_seconds)
        logger.info("Export Worker 已停止")


def _job_id(payload: object, aggregate_id: UUID) -> UUID:
    if isinstance(payload, dict):
        value = payload.get("export_job_id")
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                pass
    return aggregate_id


def _retry_delay(attempts: int, *, base_seconds: float, max_seconds: float) -> timedelta:
    exponent = max(0, attempts - 1)
    return timedelta(seconds=min(base_seconds * (2**exponent), max_seconds))


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP ExportJob outbox Worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="最多处理一个事件后退出")
    mode.add_argument("--drain", action="store_true", help="处理到当前无可用事件后退出")
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    settings.validate_runtime_security()
    worker = ExportWorker(
        factory=create_session_factory(get_engine()),
        settings=settings,
        storage=S3MultipartStorage(settings),
    )
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event, once=args.once, drain=args.drain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
