"""服务端上传网关 Protocol（待 V31-012b 接入真实 DDP Multipart Upload API）。

契约严格对齐 ``backend/app/api/v1/uploads.py`` 与 ``upload_schemas.py``：
init/resume session -> get presigned part URL -> PUT part & capture ETag ->
ack part -> complete -> 返回 RawAsset 回执。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class CreateSessionRequest:
    project_id: str
    capture_session_id: str
    collection_task_id: str | None
    original_filename: str
    declared_content_type: str
    declared_size_bytes: int
    expected_sha256: str
    format_hint: str
    idempotency_key: str
    part_size_hint_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemotePartSummary:
    part_number: int
    size_bytes: int
    etag: str
    sha256: str


@dataclass(frozen=True)
class RemoteUploadSession:
    upload_session_id: str
    version: int
    part_size_bytes: int
    expires_at: datetime
    status: str
    uploaded_parts: tuple[RemotePartSummary, ...] = ()
    asset_id: str | None = None


@dataclass(frozen=True)
class SignedPartUrl:
    part_number: int
    method: str
    url: str
    headers: Mapping[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class PartAckResult:
    part_number: int
    etag: str
    size_bytes: int
    sha256: str
    uploaded_bytes: int
    uploaded_part_count: int


@dataclass(frozen=True)
class CompletePartSpec:
    part_number: int
    etag: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class CompleteSessionRequest:
    upload_session_id: str
    expected_version: int
    expected_sha256: str
    expected_size_bytes: int
    parts: tuple[CompletePartSpec, ...]
    idempotency_key: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadReceipt:
    raw_asset_id: str
    sha256: str
    ingestion_status: str
    deduplicated: bool = False
    replayed: bool = False


class ServerGateway(Protocol):
    """Edge Agent 与 DDP 服务端的上传交互抽象（纯出站拉取/上传语义）。"""

    def create_or_resume_session(self, req: CreateSessionRequest) -> RemoteUploadSession: ...

    def get_session(self, upload_session_id: str) -> RemoteUploadSession: ...

    def get_part_url(
        self, upload_session_id: str, part_number: int, *, sha256: str
    ) -> SignedPartUrl: ...

    def upload_part(self, signed_url: SignedPartUrl, data: bytes) -> str:
        """向预签名 URL 上传分片字节并返回 ETag。"""
        ...

    def ack_part(
        self,
        upload_session_id: str,
        part_number: int,
        *,
        etag: str,
        size_bytes: int,
        sha256: str,
    ) -> PartAckResult: ...

    def complete_session(self, req: CompleteSessionRequest) -> UploadReceipt: ...
