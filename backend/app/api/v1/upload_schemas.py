"""上传会话、分片与 RawAsset API DTO。

请求模型 ``extra="forbid"``；客户端不能提交 project_id、status、版本或对象 Key。
响应字段与阶段 2 JSON Schema 草案对齐。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.ingestion.ports import (
    AckUploadPartResult,
    CompleteUploadResult,
    UploadSessionDetail,
)
from app.domain.ingestion.models import RawAsset, UploadPart, UploadSession
from app.domain.ingestion.validation import required_part_count

_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_FORMAT_PATTERN = r"^(MCAP|ROSBAG2|HDF5|MP4|PARQUET|CSV|JSON|ZIP|UNKNOWN)$"


class UploadSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_filename: str = Field(min_length=1, max_length=255)
    declared_content_type: str = Field(min_length=1, max_length=200)
    declared_size_bytes: int = Field(gt=0)
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    capture_session_id: UUID | None = None
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    format_hint: str | None = Field(default=None, pattern=_FORMAT_PATTERN)
    part_size_bytes: int | None = Field(default=None, gt=0)
    metadata: dict[str, object] = Field(default_factory=dict)


class UploadPartUrlRequest(BaseModel):
    """可选的分片预签名校验和绑定；省略时保持旧客户端行为。"""

    model_config = ConfigDict(extra="forbid")

    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class UploadPartAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    etag: str = Field(min_length=1, max_length=256)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class UploadPartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=256)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_size_bytes: int = Field(gt=0)
    parts: list[UploadPartRequest] = Field(min_length=1, max_length=10000)
    metadata: dict[str, object] = Field(default_factory=dict)


class AbortUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class UploadPartSummaryOut(BaseModel):
    part_number: int
    size_bytes: int
    etag: str
    sha256: str
    recorded_at: datetime

    @classmethod
    def from_domain(cls, part: UploadPart) -> UploadPartSummaryOut:
        return cls(
            part_number=part.part_number,
            size_bytes=part.size_bytes,
            etag=part.etag,
            sha256=part.sha256,
            recorded_at=part.recorded_at,
        )


class UploadSessionOut(BaseModel):
    upload_session_id: UUID
    project_id: UUID
    collection_task_id: UUID | None
    batch_id: UUID | None
    capture_session_id: UUID | None
    status: str
    verification_status: str
    verification_error_code: str | None
    verified_at: datetime | None
    original_filename: str
    declared_content_type: str
    declared_size_bytes: int
    expected_sha256: str | None
    format_hint: str | None
    part_size_bytes: int
    max_size_bytes: int
    allowed_content_types: list[str]
    object_key_prefix: str
    object_key: str
    expires_at: datetime
    completed_at: datetime | None
    aborted_at: datetime | None
    failure_code: str | None
    failure_summary: str | None
    uploaded_bytes: int
    uploaded_part_count: int
    missing_part_numbers: list[int]
    uploaded_parts: list[UploadPartSummaryOut]
    deduplicated: bool
    deduplicated_asset_id: UUID | None
    asset_id: UUID | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None

    @classmethod
    def from_detail(cls, detail: UploadSessionDetail) -> UploadSessionOut:
        return _session_out(
            detail.session,
            uploaded_bytes=detail.uploaded_bytes,
            uploaded_part_count=detail.uploaded_part_count,
            missing=detail.missing_part_numbers,
            parts=[UploadPartSummaryOut.from_domain(item) for item in detail.parts],
        )

    @classmethod
    def from_session(cls, session: UploadSession) -> UploadSessionOut:
        total = required_part_count(
            session.declared_size_bytes,
            session.part_size_bytes,
        )
        missing = list(range(session.uploaded_part_count + 1, total + 1))
        return _session_out(
            session,
            uploaded_bytes=session.uploaded_bytes,
            uploaded_part_count=session.uploaded_part_count,
            missing=missing,
            parts=[],
        )


class UploadPartUrlOut(BaseModel):
    part_number: int
    method: str
    url: str
    headers: dict[str, str]
    expires_at: datetime


class UploadPartAckOut(BaseModel):
    part_number: int
    etag: str
    size_bytes: int
    sha256: str
    recorded_at: datetime
    uploaded_bytes: int
    uploaded_part_count: int

    @classmethod
    def from_result(cls, result: AckUploadPartResult) -> UploadPartAckOut:
        return cls(
            part_number=result.part.part_number,
            etag=result.part.etag,
            size_bytes=result.part.size_bytes,
            sha256=result.part.sha256,
            recorded_at=result.part.recorded_at,
            uploaded_bytes=result.uploaded_bytes,
            uploaded_part_count=result.uploaded_part_count,
        )


class UploadPartListOut(BaseModel):
    items: list[UploadPartSummaryOut]
    missing_part_numbers: list[int]
    uploaded_bytes: int

    @classmethod
    def from_detail(cls, detail: UploadSessionDetail) -> UploadPartListOut:
        return cls(
            items=[UploadPartSummaryOut.from_domain(item) for item in detail.parts],
            missing_part_numbers=detail.missing_part_numbers,
            uploaded_bytes=detail.uploaded_bytes,
        )


class RawAssetOut(BaseModel):
    asset_id: UUID
    project_id: UUID
    code: str
    source_type: str
    collection_task_id: UUID | None
    batch_id: UUID | None
    upload_session_id: UUID | None
    capture_session_id: UUID | None
    original_filename: str
    object_key: str
    size_bytes: int
    content_type: str
    format: str
    sha256: str
    status: str
    captured_at: datetime | None
    ingested_at: datetime
    device_ids: list[UUID]
    sensor_ids: list[UUID]
    sensor_snapshot: dict[str, object] | None
    device_time_raw: dict[str, object] | None
    raw_metadata: dict[str, object]
    storage_class: str
    retention_policy_id: UUID | None
    quarantine_reason: str | None
    quarantine_codes: list[str]
    scan_report_key: str | None
    verified_at: datetime | None
    archived_at: datetime | None
    tombstoned_at: datetime | None
    duplicate_of_asset_id: UUID | None
    uploaded_by: UUID
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    # 动作按钮只按服务端下发的集合渲染：前端不自己推断"隔离资产能不能放行"。
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, asset: RawAsset) -> RawAssetOut:
        return cls(
            asset_id=asset.asset_id,
            project_id=asset.project_id,
            code=asset.code,
            source_type=_value(asset.source_type),
            collection_task_id=asset.collection_task_id,
            batch_id=asset.batch_id,
            upload_session_id=asset.upload_session_id,
            capture_session_id=asset.capture_session_id,
            original_filename=asset.original_filename,
            object_key=asset.object_key,
            size_bytes=asset.size_bytes,
            content_type=asset.content_type,
            format=_value(asset.format),
            sha256=asset.sha256,
            status=_value(asset.status),
            captured_at=asset.captured_at,
            ingested_at=asset.ingested_at,
            device_ids=list(asset.device_ids),
            sensor_ids=list(asset.sensor_ids),
            sensor_snapshot=(
                dict(asset.sensor_snapshot) if asset.sensor_snapshot is not None else None
            ),
            device_time_raw=(
                dict(asset.device_time_raw) if asset.device_time_raw is not None else None
            ),
            raw_metadata=dict(asset.raw_metadata),
            storage_class=_value(asset.storage_class),
            retention_policy_id=asset.retention_policy_id,
            quarantine_reason=asset.quarantine_reason,
            quarantine_codes=list(asset.quarantine_codes),
            scan_report_key=asset.scan_report_key,
            verified_at=asset.verified_at,
            archived_at=asset.archived_at,
            tombstoned_at=asset.tombstoned_at,
            duplicate_of_asset_id=asset.duplicate_of_asset_id,
            uploaded_by=asset.uploaded_by,
            version=asset.version,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
            created_by=asset.created_by,
            updated_by=asset.updated_by,
            allowed_actions=sorted(asset.allowed_actions),
        )


class RawAssetActionRequest(BaseModel):
    """原始资产动作载荷：原因与隔离码；其余字段一律 ``extra="forbid"``。"""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    quarantine_codes: list[str] | None = None


class CompleteUploadResponse(BaseModel):
    upload_session: UploadSessionOut
    raw_asset: RawAssetOut | None
    deduplicated: bool
    validation_job_id: UUID
    replayed: bool

    @classmethod
    def from_result(cls, result: CompleteUploadResult) -> CompleteUploadResponse:
        return cls(
            upload_session=_session_out(
                result.session,
                uploaded_bytes=result.session.uploaded_bytes,
                uploaded_part_count=result.session.uploaded_part_count,
                missing=[],
                parts=[UploadPartSummaryOut.from_domain(part) for part in result.parts],
            ),
            raw_asset=(
                RawAssetOut.from_domain(result.raw_asset) if result.raw_asset is not None else None
            ),
            deduplicated=result.deduplicated,
            validation_job_id=result.validation_job_id,
            replayed=result.replayed,
        )


def _session_out(
    session: UploadSession,
    *,
    uploaded_bytes: int,
    uploaded_part_count: int,
    missing: list[int],
    parts: list[UploadPartSummaryOut],
) -> UploadSessionOut:
    prefix = session.object_key.rsplit("/", 1)[0] + "/"
    return UploadSessionOut(
        upload_session_id=session.upload_session_id,
        project_id=session.project_id,
        collection_task_id=session.collection_task_id,
        batch_id=session.batch_id,
        capture_session_id=session.capture_session_id,
        status=_value(session.status),
        verification_status=_value(session.verification_status),
        verification_error_code=session.verification_error_code,
        verified_at=session.verified_at,
        original_filename=session.original_filename,
        declared_content_type=session.declared_content_type,
        declared_size_bytes=session.declared_size_bytes,
        expected_sha256=session.expected_sha256,
        format_hint=(_value(session.format_hint) if session.format_hint is not None else None),
        part_size_bytes=session.part_size_bytes,
        max_size_bytes=session.max_size_bytes,
        allowed_content_types=list(session.allowed_content_types),
        object_key_prefix=prefix,
        object_key=session.object_key,
        expires_at=session.expires_at,
        completed_at=session.completed_at,
        aborted_at=session.aborted_at,
        failure_code=session.failure_code,
        failure_summary=session.failure_summary,
        uploaded_bytes=uploaded_bytes,
        uploaded_part_count=uploaded_part_count,
        missing_part_numbers=missing,
        uploaded_parts=parts,
        deduplicated=session.deduplicated,
        deduplicated_asset_id=session.deduplicated_asset_id,
        asset_id=session.asset_id,
        version=session.version,
        created_at=session.created_at,
        updated_at=session.updated_at,
        created_by=session.created_by,
    )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


CompleteUploadResponse.model_rebuild()
