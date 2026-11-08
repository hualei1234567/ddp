"""上传/RawAsset 应用层端口与数据契约。

所有仓储方法必须接收服务端构造的 :class:`ProjectScope`；按 ID 查询必须带项目
过滤，跨项目资源返回 ``None`` 并由用例统一映射为 404。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.ingestion.enums import IngestionJobStatus, RawAssetStatus
from app.domain.ingestion.jobs import IngestionJob
from app.domain.ingestion.models import RawAsset, RawAssetLink, UploadPart, UploadSession


class UnitOfWork(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UploadSessionRepository(Protocol):
    def add(self, scope: ProjectScope, session: UploadSession) -> UploadSession: ...

    def get(self, scope: ProjectScope, session_id: UUID) -> UploadSession | None: ...

    def save(
        self,
        scope: ProjectScope,
        session: UploadSession,
        *,
        expected_version: int,
    ) -> UploadSession: ...

    def set_progress(
        self,
        scope: ProjectScope,
        session_id: UUID,
        *,
        uploaded_bytes: int,
        uploaded_part_count: int,
        status: str | None = None,
        now: datetime,
    ) -> UploadSession: ...


class UploadPartRepository(Protocol):
    def add(self, scope: ProjectScope, part: UploadPart) -> UploadPart: ...

    def get(
        self,
        scope: ProjectScope,
        session_id: UUID,
        part_number: int,
    ) -> UploadPart | None: ...

    def list(self, scope: ProjectScope, session_id: UUID) -> list[UploadPart]: ...


class UploadPartChecksumBindingRepository(Protocol):
    """记录最近一次分片预签名 URL 声明的校验和。

    URL 请求可能早于 ack，绑定值必须持久化；重复签名以最新值为准。
    """

    def get(
        self,
        scope: ProjectScope,
        session_id: UUID,
        part_number: int,
    ) -> str | None: ...

    def upsert(
        self,
        scope: ProjectScope,
        *,
        session_id: UUID,
        part_number: int,
        sha256: str,
        now: datetime,
    ) -> str: ...


class RawAssetRepository(Protocol):
    def add(self, scope: ProjectScope, asset: RawAsset) -> RawAsset: ...

    def get(self, scope: ProjectScope, asset_id: UUID) -> RawAsset | None: ...

    def get_for_update(self, scope: ProjectScope, asset_id: UUID) -> RawAsset | None: ...

    def find_active_by_content(
        self,
        scope: ProjectScope,
        *,
        sha256: str,
        size_bytes: int,
    ) -> RawAsset | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: RawAssetStatus | None = None,
        collection_task_id: UUID | None = None,
        format: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[RawAsset]: ...


class RawAssetLinkRepository(Protocol):
    def add(self, scope: ProjectScope, link: RawAssetLink) -> RawAssetLink: ...

    def exists_for_session(
        self,
        scope: ProjectScope,
        *,
        asset_id: UUID,
        upload_session_id: UUID,
        link_type: str,
    ) -> bool: ...

    def list_for_asset(self, scope: ProjectScope, asset_id: UUID) -> list[RawAssetLink]: ...


class CollectionDirectory(Protocol):
    """校验上传会话引用的采集任务/批次；只返回存在性。"""

    def task_exists(self, scope: ProjectScope, task_id: UUID) -> bool: ...

    def batch_belongs(
        self,
        scope: ProjectScope,
        *,
        batch_id: UUID,
        task_id: UUID,
    ) -> bool: ...


class IngestionJobRepository(Protocol):
    def add(self, scope: ProjectScope, job: IngestionJob) -> IngestionJob: ...

    def get(self, scope: ProjectScope, job_id: UUID) -> IngestionJob | None: ...

    def get_by_idempotency_key(
        self,
        scope: ProjectScope,
        key: str,
    ) -> IngestionJob | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: IngestionJobStatus | None = None,
        raw_asset_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[IngestionJob]: ...

    def save(
        self,
        scope: ProjectScope,
        job: IngestionJob,
        *,
        expected_version: int,
    ) -> IngestionJob: ...


@dataclass(frozen=True, slots=True)
class ClaimedValidationEvent:
    """已被 Worker 锁定的一条校验事件。

    只携带处理所需字段，不暴露 ORM 行；事件在事务提交前保持行锁。
    """

    event_id: UUID
    project_id: UUID
    aggregate_id: UUID
    payload: Mapping[str, object]
    attempts: int
    created_at: datetime


class ValidationEventQueue(Protocol):
    """校验 outbox 事件消费端口。

    PostgreSQL 使用 ``FOR UPDATE SKIP LOCKED`` 抢占；SQLite 串行执行。
    业务写入与事件状态更新必须在同一事务内完成。
    """

    def claim_next(
        self,
        *,
        now: datetime,
        event_type: str,
    ) -> ClaimedValidationEvent | None: ...

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


@dataclass(frozen=True, slots=True)
class StoredPart:
    part_number: int
    etag: str
    size_bytes: int
    checksum_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PresignedPart:
    url: str
    headers: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PresignedDownload:
    """短时下载地址；URL 属于敏感凭据，不得写入日志或审计明文。"""

    url: str
    expires_at: datetime


class ObjectStorage(Protocol):
    """对象存储 multipart 适配端口；耗时读写不得在 HTTP 线程执行。"""

    def initiate_multipart(self, object_key: str) -> str: ...

    def presign_upload_part(
        self,
        object_key: str,
        upload_id: str,
        *,
        part_number: int,
        content_length: int,
        checksum_sha256_base64: str | None,
        expires_in_seconds: int,
    ) -> PresignedPart: ...

    def presign_download(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> PresignedDownload: ...

    def list_parts(self, object_key: str, upload_id: str) -> list[StoredPart]: ...

    def complete_multipart(
        self,
        object_key: str,
        upload_id: str,
        parts: list[StoredPart],
    ) -> None: ...

    def abort_multipart(self, object_key: str, upload_id: str) -> None: ...

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes: ...

    def stream_object(self, object_key: str, *, chunk_size: int) -> Iterator[bytes]:
        """按块流式读取对象；调用方不得在 HTTP 线程消费大文件。"""
        ...

    def delete_object(self, object_key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """本次会话的服务端策略；阈值来自配置，不在用例内硬编码。"""

    max_size_bytes: int
    part_size_bytes: int
    allowed_content_types: frozenset[str]
    session_ttl: timedelta
    url_ttl: timedelta

    def __post_init__(self) -> None:
        if self.max_size_bytes <= 0 or self.part_size_bytes <= 0:
            raise ValueError("上传大小策略必须为正数")
        if not self.allowed_content_types:
            raise ValueError("上传内容类型白名单不能为空")
        if self.session_ttl.total_seconds() <= 0 or self.url_ttl.total_seconds() <= 0:
            raise ValueError("上传有效期策略必须为正数")


@dataclass(frozen=True, slots=True)
class CreateUploadSessionCommand:
    original_filename: str
    declared_content_type: str
    declared_size_bytes: int
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    expected_sha256: str | None = None
    format_hint: str | None = None
    part_size_bytes: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AckUploadPartCommand:
    etag: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompletePartSpec:
    part_number: int
    etag: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompleteUploadCommand:
    expected_sha256: str
    expected_size_bytes: int
    parts: tuple[CompletePartSpec, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AbortUploadSessionCommand:
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class UploadSessionDetail:
    session: UploadSession
    parts: list[UploadPart]
    uploaded_bytes: int
    uploaded_part_count: int
    missing_part_numbers: list[int]


@dataclass(frozen=True, slots=True)
class CreatedUploadSession:
    session: UploadSession
    replayed: bool


@dataclass(frozen=True, slots=True)
class AckUploadPartResult:
    part: UploadPart
    uploaded_bytes: int
    uploaded_part_count: int


@dataclass(frozen=True, slots=True)
class CompleteUploadResult:
    session: UploadSession
    raw_asset: RawAsset | None
    deduplicated: bool
    validation_job_id: UUID
    replayed: bool
    parts: tuple[UploadPart, ...] = ()
