"""Spool 状态机、条目与上传恢复游标模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SpoolState(StrEnum):
    TMP = "TMP"
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


STATE_SUBDIR: dict[SpoolState, str] = {
    SpoolState.TMP: "tmp",
    SpoolState.PENDING: "pending",
    SpoolState.UPLOADING: "uploading",
    SpoolState.UPLOADED: "uploaded",
    SpoolState.FAILED: "failed",
    SpoolState.QUARANTINED: "quarantine",
}

ALLOWED_TRANSITIONS: dict[SpoolState, frozenset[SpoolState]] = {
    SpoolState.TMP: frozenset({SpoolState.PENDING, SpoolState.FAILED, SpoolState.QUARANTINED}),
    SpoolState.PENDING: frozenset(
        {SpoolState.UPLOADING, SpoolState.FAILED, SpoolState.QUARANTINED}
    ),
    SpoolState.UPLOADING: frozenset(
        {SpoolState.UPLOADED, SpoolState.PENDING, SpoolState.FAILED, SpoolState.QUARANTINED}
    ),
    SpoolState.FAILED: frozenset({SpoolState.PENDING, SpoolState.QUARANTINED}),
    SpoolState.UPLOADED: frozenset(),
    SpoolState.QUARANTINED: frozenset(),
}


@dataclass(frozen=True)
class SpoolArtifactRecord:
    rel_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    format: str
    duration_ms: int | None = None


@dataclass(frozen=True)
class ArtifactReceipt:
    artifact_path: str
    raw_asset_id: str
    sha256: str
    ingestion_status: str
    deduplicated: bool = False


@dataclass(frozen=True)
class ServerReceipt:
    """服务端回执：仅当全部 artifact 均获得服务端校验回执后，条目才允许进入 UPLOADED。"""

    received_at: datetime
    artifacts: tuple[ArtifactReceipt, ...]


@dataclass(frozen=True)
class SpoolEntry:
    entry_id: str
    capture_session_id: str
    collection_task_id: str | None
    project_id: str
    state: SpoolState
    manifest_hash: str | None
    total_bytes: int
    artifacts: tuple[SpoolArtifactRecord, ...]
    server_receipt: ServerReceipt | None
    failure_reason: str | None
    quarantine_reason: str | None
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class UploadPartRecord:
    entry_id: str
    artifact_path: str
    part_number: int
    offset_bytes: int
    size_bytes: int
    sha256: str
    etag: str | None
    acked: bool


@dataclass(frozen=True)
class UploadCursor:
    """单个 artifact 的可续传分片上传状态（持久化于本地 SQLite）。"""

    entry_id: str
    artifact_path: str
    upload_session_id: str
    session_version: int
    part_size_bytes: int
    total_parts: int
    expires_at: datetime
    idempotency_key: str
    raw_asset_id: str | None = None
    server_sha256: str | None = None
    ingestion_status: str | None = None
    deduplicated: bool = False
    parts: tuple[UploadPartRecord, ...] = ()


@dataclass(frozen=True)
class SpoolResidueSummary:
    entry_count: int
    total_bytes: int
    session_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryReport:
    cleaned_tmp_ids: tuple[str, ...] = field(default_factory=tuple)
    promoted_pending_ids: tuple[str, ...] = field(default_factory=tuple)
    resumable_uploading_ids: tuple[str, ...] = field(default_factory=tuple)
    quarantined_ids: tuple[str, ...] = field(default_factory=tuple)
    orphan_dirs_cleaned: tuple[str, ...] = field(default_factory=tuple)
