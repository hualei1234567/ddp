"""服务容器：把配置、存储、回调与运行队列装配在一起，便于测试注入。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app import frames
from app.callbacks import CallbackClient
from app.config import Settings
from app.runs import JobRunner, RunRecord, RunRegistry
from app.storage import S3Storage

logger = logging.getLogger("ddp.local_adapter.container")


@dataclass
class ServiceContainer:
    settings: Settings
    storage: S3Storage
    callbacks: CallbackClient
    registry: RunRegistry = field(default_factory=RunRegistry)
    runner: JobRunner | None = None
    ffmpeg_info: str | None = None

    def __post_init__(self) -> None:
        if self.runner is None:
            self.runner = JobRunner(
                max_workers=self.settings.max_concurrent_runs,
                max_pending=self.settings.max_pending_runs,
            )
        if self.ffmpeg_info is None:
            self.ffmpeg_info = frames.ffmpeg_version(self.settings.ffmpeg_path)

    @classmethod
    def build(cls, settings: Settings) -> "ServiceContainer":
        return cls(
            settings=settings,
            storage=S3Storage(settings),
            callbacks=CallbackClient(settings),
        )

    def submit(self, record: RunRecord, fn) -> bool:
        assert self.runner is not None
        created = self.registry.create(record)
        if self.runner.submit(record.run_id, fn):
            return True
        self.registry.update(
            created.run_id, status="REJECTED", error_code="adapter_queue_full"
        )
        return False

    def close(self) -> None:
        if self.runner is not None:
            self.runner.shutdown()
        self.callbacks.close()
        self.storage.close()


__all__ = ["ServiceContainer"]
