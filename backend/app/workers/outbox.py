"""RawAsset 校验 Worker 入口。

运行方式::

    python -m app.workers.outbox

消费 outbox 中的 ``ingestion.raw_asset.validation_requested`` 事件。每个事件在
独立事务中完成：抢占行 → 流式重算 Hash/大小 → 迁移资产 → 审计 → 发
``ingestion.raw_asset.validated`` → 标记源事件 PUBLISHED。

Worker 与 HTTP API 使用同一镜像，但独立进程运行；HTTP 线程不读取大文件。
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion.ports import ObjectStorage
from app.application.ingestion.raw_asset_validation import RawAssetValidationService
from app.application.ingestion.validation_events import (
    ProcessingStatus,
    ProcessResult,
    ValidationEventProcessor,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.session import create_session_factory, get_engine
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.outbox_validation import SqlOutboxValidationQueue
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.infrastructure.storage.s3 import S3MultipartStorage

logger = logging.getLogger("ddp.worker.outbox")


def build_validation_processor(
    session: Session,
    settings: Settings,
    storage: ObjectStorage,
) -> ValidationEventProcessor:
    """按当前会话装配一次校验处理器；事务由处理器持有。"""
    validation = RawAssetValidationService(
        assets=SqlRawAssetRepository(session),
        storage=storage,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
    )
    return ValidationEventProcessor(
        validation=validation,
        queue=SqlOutboxValidationQueue(session),
        uow=session,
        max_attempts=settings.validation_max_attempts,
        retry_base_seconds=settings.validation_retry_base_seconds,
        retry_max_seconds=settings.validation_retry_max_seconds,
        chunk_size_bytes=settings.validation_stream_chunk_size_bytes,
    )


class OutboxWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        storage: ObjectStorage,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._storage = storage

    def process_once(self, *, now: datetime | None = None) -> ProcessResult:
        with self._factory() as session:
            processor = build_validation_processor(session, self._settings, self._storage)
            result = processor.process_next(now=now)
        _log_result(result)
        return result

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        once: bool = False,
        drain: bool = False,
    ) -> None:
        stop = stop_event or threading.Event()
        logger.info(
            "RawAsset 校验 Worker 已启动 poll_interval=%s max_attempts=%s",
            self._settings.validation_worker_poll_interval_seconds,
            self._settings.validation_max_attempts,
        )
        while not stop.is_set():
            try:
                result = self.process_once()
            except Exception:  # noqa: BLE001 - 单事件异常不得终止 Worker
                logger.exception("处理校验事件时发生未预期异常，将在下一轮重试")
                if once or drain:
                    return
                stop.wait(self._settings.validation_worker_poll_interval_seconds)
                continue
            if once:
                return
            if drain and result.status is ProcessingStatus.IDLE:
                return
            if result.status is ProcessingStatus.IDLE:
                stop.wait(self._settings.validation_worker_poll_interval_seconds)
        logger.info("RawAsset 校验 Worker 已停止")


def _log_result(result: ProcessResult) -> None:
    if result.status is ProcessingStatus.IDLE:
        return
    if result.status is ProcessingStatus.PUBLISHED:
        logger.info(
            "校验事件已发布 event_id=%s outcome=%s attempts=%s",
            result.event_id,
            result.outcome.value if result.outcome else None,
            result.attempts,
        )
        return
    if result.status is ProcessingStatus.RETRIED:
        logger.warning(
            "校验事件将重试 event_id=%s attempts=%s error=%s",
            result.event_id,
            result.attempts,
            result.error,
        )
        return
    logger.error(
        "校验事件进入死信 event_id=%s attempts=%s error=%s",
        result.event_id,
        result.attempts,
        result.error,
    )


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP RawAsset 校验 outbox Worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="最多处理一个事件后退出")
    mode.add_argument("--drain", action="store_true", help="处理到当前无可用事件后退出")
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    settings.validate_runtime_security()
    storage = S3MultipartStorage(settings)
    worker = OutboxWorker(
        factory=create_session_factory(get_engine()),
        settings=settings,
        storage=storage,
    )

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event, once=args.once, drain=args.drain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
