"""ExportJob 应用层端口。

仓储与队列实现必须接收服务端 ``ProjectScope``；所有按 ID 查询强制项目过滤。
对象存储端口复用现有 S3/MinIO 适配能力，禁止把大文件写入数据库。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.datasets.export_jobs import ExportJob
from app.domain.identity.models import ProjectScope


class ExportJobRepository(Protocol):
    def add(self, scope: ProjectScope, job: ExportJob) -> ExportJob: ...

    def get(self, scope: ProjectScope, job_id: UUID) -> ExportJob | None: ...

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        idempotency_key: str,
    ) -> ExportJob | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        dataset_version_id: UUID | None = None,
        status: str | None = None,
        format: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ExportJob]: ...

    def save(
        self,
        scope: ProjectScope,
        job: ExportJob,
        *,
        expected_version: int,
    ) -> ExportJob: ...


@dataclass(frozen=True, slots=True)
class ClaimedExportEvent:
    event_id: UUID
    project_id: UUID
    aggregate_id: UUID
    payload: Mapping[str, object]
    attempts: int
    created_at: datetime


class ExportQueue(Protocol):
    """outbox 消费端口；业务状态与事件状态必须在同一事务内更新。"""

    def claim_next(
        self,
        *,
        now: datetime,
        event_type: str,
    ) -> ClaimedExportEvent | None: ...

    def mark_published(self, event_id: UUID, *, now: datetime) -> None: ...

    def mark_retry(
        self,
        event_id: UUID,
        *,
        attempts: int,
        available_at: datetime,
        last_error: str,
    ) -> bool: ...

    def mark_dead_letter(
        self,
        event_id: UUID,
        *,
        attempts: int,
        last_error: str,
    ) -> bool: ...


class ExportObjectStorage(Protocol):
    """导出产物对象存储端口；大文件读写只能在 Worker 中执行。"""

    def put_bytes(
        self,
        object_key: str,
        data: bytes,
        *,
        content_type: str,
    ) -> None: ...

    def stream_object(self, object_key: str, *, chunk_size: int) -> Iterator[bytes]: ...

    def presign_download(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ): ...

    def delete_object(self, object_key: str) -> None: ...
