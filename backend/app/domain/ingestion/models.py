"""上传会话、分片、RawAsset 与使用关系聚合。

实体不可变；状态只能经具名动作迁移。跨对象前置条件（项目、任务、对象存储结果）
由应用层校验。RawAsset 内容字段创建后不可变。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.ingestion.enums import (
    RawAssetFormat,
    RawAssetLinkStatus,
    RawAssetLinkType,
    RawAssetQuarantineCode,
    RawAssetSourceType,
    RawAssetStatus,
    StorageClass,
    UploadSessionAction,
    UploadSessionStatus,
    UploadVerificationStatus,
)
from app.domain.ingestion.errors import (
    DomainValidationError,
    ResourceImmutableError,
    UploadSessionExpiredError,
)
from app.domain.ingestion.state_machines import (
    assert_upload_session_action,
    raw_asset_target_status,
)
from app.domain.ingestion.validation import (
    CONTENT_TYPE_MAX_LENGTH,
    canonical_json,
    normalize_etag,
    normalize_sha256,
    sanitize_filename,
    validate_part_number,
    validate_raw_object_key,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区（UTC）")
    return value


def _optional_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _aware(value, field_name)


def _positive(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def _non_negative(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def _optional_uuid(value: UUID | None, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


@dataclass(frozen=True, slots=True)
class UploadSession:
    upload_session_id: UUID
    project_id: UUID
    original_filename: str
    declared_content_type: str
    declared_size_bytes: int
    part_size_bytes: int
    max_size_bytes: int
    object_key: str
    multipart_upload_id: str
    expires_at: datetime
    allowed_content_types: tuple[str, ...]
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    status: UploadSessionStatus | str = UploadSessionStatus.CREATED
    expected_sha256: str | None = None
    format_hint: RawAssetFormat | str | None = None
    verification_status: UploadVerificationStatus | str = UploadVerificationStatus.PENDING
    verification_error_code: str | None = None
    verified_at: datetime | None = None
    completed_at: datetime | None = None
    aborted_at: datetime | None = None
    failure_code: str | None = None
    failure_summary: str | None = None
    uploaded_bytes: int = 0
    uploaded_part_count: int = 0
    deduplicated: bool = False
    deduplicated_asset_id: UUID | None = None
    asset_id: UUID | None = None
    validation_job_id: UUID | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_filename", sanitize_filename(self.original_filename))
        object.__setattr__(
            self,
            "declared_content_type",
            _content_type(self.declared_content_type),
        )
        object.__setattr__(
            self,
            "declared_size_bytes",
            _positive(self.declared_size_bytes, "declared_size_bytes"),
        )
        object.__setattr__(
            self,
            "part_size_bytes",
            _positive(self.part_size_bytes, "part_size_bytes"),
        )
        object.__setattr__(self, "max_size_bytes", _positive(self.max_size_bytes, "max_size_bytes"))
        if self.max_size_bytes < self.declared_size_bytes:
            raise DomainValidationError("declared_size_bytes 不得超过 max_size_bytes")
        if not isinstance(self.object_key, str) or not validate_raw_object_key(self.object_key):
            raise DomainValidationError("object_key 不符合原始内容 Key 规范")
        if not isinstance(self.multipart_upload_id, str) or not self.multipart_upload_id.strip():
            raise DomainValidationError("multipart_upload_id 不能为空")
        object.__setattr__(self, "status", _enum(self.status, UploadSessionStatus, "status"))
        object.__setattr__(
            self,
            "verification_status",
            _enum(self.verification_status, UploadVerificationStatus, "verification_status"),
        )
        object.__setattr__(
            self,
            "expected_sha256",
            normalize_sha256(self.expected_sha256, required=False),
        )
        if self.format_hint is not None:
            object.__setattr__(
                self,
                "format_hint",
                _enum(self.format_hint, RawAssetFormat, "format_hint"),
            )
        for name in (
            "collection_task_id",
            "batch_id",
            "deduplicated_asset_id",
            "asset_id",
            "validation_job_id",
        ):
            object.__setattr__(self, name, _optional_uuid(getattr(self, name), name))
        if self.batch_id is not None and self.collection_task_id is None:
            raise DomainValidationError("batch_id 必须与 collection_task_id 同时提供")
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))
        for name in ("verified_at", "completed_at", "aborted_at", "created_at", "updated_at"):
            object.__setattr__(self, name, _optional_aware(getattr(self, name), name))
        for name in ("uploaded_bytes", "uploaded_part_count"):
            object.__setattr__(self, name, _non_negative(getattr(self, name), name))
        allowed = tuple(self.allowed_content_types)
        if not allowed:
            raise DomainValidationError("allowed_content_types 不能为空")
        if len(set(allowed)) != len(allowed):
            raise DomainValidationError("allowed_content_types 不得重复")
        object.__setattr__(
            self,
            "allowed_content_types",
            tuple(_content_type(item) for item in allowed),
        )
        object.__setattr__(self, "version", _positive(self.version, "version"))
        for name in ("failure_code", "failure_summary", "verification_error_code"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DomainValidationError(f"{name} 必须是非空字符串或 null")
        if self.status is UploadSessionStatus.COMPLETED and self.completed_at is None:
            raise DomainValidationError("COMPLETED 会话必须记录 completed_at")
        if self.status is UploadSessionStatus.ABORTED and self.aborted_at is None:
            raise DomainValidationError("ABORTED 会话必须记录 aborted_at")

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            UploadSessionStatus.COMPLETED,
            UploadSessionStatus.ABORTED,
            UploadSessionStatus.EXPIRED,
            UploadSessionStatus.FAILED,
        }

    def assert_not_expired(self, now: datetime) -> None:
        if (
            self.status in {UploadSessionStatus.CREATED, UploadSessionStatus.UPLOADING}
            and now >= self.expires_at
        ):
            raise UploadSessionExpiredError("上传会话已过期，请新建会话")

    def begin_upload(self, now: datetime) -> UploadSession:
        target = assert_upload_session_action(self.status, UploadSessionAction.BEGIN_UPLOAD.value)
        return replace(self, status=target, updated_at=now)

    def begin_complete(self, now: datetime) -> UploadSession:
        target = assert_upload_session_action(self.status, UploadSessionAction.COMPLETE.value)
        return replace(self, status=target, updated_at=now)

    def mark_completed(
        self,
        *,
        asset_id: UUID,
        deduplicated: bool,
        deduplicated_asset_id: UUID | None,
        validation_job_id: UUID,
        now: datetime,
    ) -> UploadSession:
        target = assert_upload_session_action(
            self.status,
            UploadSessionAction.SYSTEM_REGISTER.value,
        )
        return replace(
            self,
            status=target,
            completed_at=now,
            updated_at=now,
            asset_id=asset_id,
            deduplicated=deduplicated,
            deduplicated_asset_id=deduplicated_asset_id,
            validation_job_id=validation_job_id,
            failure_code=None,
            failure_summary=None,
        )

    def retry_upload(
        self,
        *,
        failure_code: str,
        failure_summary: str,
        now: datetime,
    ) -> UploadSession:
        target = assert_upload_session_action(
            self.status,
            UploadSessionAction.SYSTEM_RETRYABLE_FAILURE.value,
        )
        return replace(
            self,
            status=target,
            updated_at=now,
            failure_code=failure_code,
            failure_summary=failure_summary,
        )

    def mark_failed(
        self,
        *,
        failure_code: str,
        failure_summary: str,
        now: datetime,
    ) -> UploadSession:
        target = assert_upload_session_action(
            self.status,
            UploadSessionAction.SYSTEM_FAIL.value,
        )
        return replace(
            self,
            status=target,
            updated_at=now,
            failure_code=failure_code,
            failure_summary=failure_summary,
        )

    def abort(self, *, reason: str | None, now: datetime) -> UploadSession:
        target = assert_upload_session_action(self.status, UploadSessionAction.ABORT.value)
        return replace(
            self,
            status=target,
            aborted_at=now,
            updated_at=now,
            failure_summary=reason,
        )

    def expire(self, now: datetime) -> UploadSession:
        target = assert_upload_session_action(self.status, UploadSessionAction.SYSTEM_EXPIRE.value)
        return replace(
            self,
            status=target,
            updated_at=now,
            failure_code="upload_session_expired",
            failure_summary="上传会话已过期",
        )


@dataclass(frozen=True, slots=True)
class UploadPart:
    project_id: UUID
    upload_session_id: UUID
    part_number: int
    size_bytes: int
    etag: str
    sha256: str
    recorded_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_number", validate_part_number(self.part_number))
        object.__setattr__(self, "size_bytes", _positive(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "etag", normalize_etag(self.etag))
        normalized_sha = normalize_sha256(self.sha256)
        assert normalized_sha is not None
        object.__setattr__(self, "sha256", normalized_sha)
        object.__setattr__(self, "recorded_at", _aware(self.recorded_at, "recorded_at"))
        object.__setattr__(self, "version", _positive(self.version, "version"))


@dataclass(frozen=True, slots=True)
class RawAsset:
    asset_id: UUID
    project_id: UUID
    code: str
    source_type: RawAssetSourceType | str
    original_filename: str
    object_key: str
    size_bytes: int
    content_type: str
    format: RawAssetFormat | str
    sha256: str
    uploaded_by: UUID
    ingested_at: datetime
    storage_class: StorageClass | str = StorageClass.STANDARD
    status: RawAssetStatus | str = RawAssetStatus.VALIDATING
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    upload_session_id: UUID | None = None
    captured_at: datetime | None = None
    device_ids: tuple[UUID, ...] = ()
    sensor_ids: tuple[UUID, ...] = ()
    sensor_snapshot: Mapping[str, object] | None = None
    device_time_raw: Mapping[str, object] | None = None
    raw_metadata: Mapping[str, object] = field(default_factory=dict)
    retention_policy_id: UUID | None = None
    quarantine_reason: str | None = None
    quarantine_codes: tuple[str, ...] = ()
    scan_report_key: str | None = None
    verified_at: datetime | None = None
    archived_at: datetime | None = None
    tombstoned_at: datetime | None = None
    duplicate_of_asset_id: UUID | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_filename", sanitize_filename(self.original_filename))
        if not isinstance(self.object_key, str) or not validate_raw_object_key(self.object_key):
            raise DomainValidationError("object_key 不符合原始内容 Key 规范")
        parts = self.object_key.split("/")
        if parts[1] != str(self.project_id) or parts[5] != str(self.asset_id):
            raise DomainValidationError("object_key 的项目或资产 ID 与资源不一致")
        object.__setattr__(self, "size_bytes", _positive(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "content_type", _content_type(self.content_type))
        object.__setattr__(
            self,
            "source_type",
            _enum(self.source_type, RawAssetSourceType, "source_type"),
        )
        object.__setattr__(self, "format", _enum(self.format, RawAssetFormat, "format"))
        object.__setattr__(self, "status", _enum(self.status, RawAssetStatus, "status"))
        object.__setattr__(
            self,
            "storage_class",
            _enum(self.storage_class, StorageClass, "storage_class"),
        )
        normalized_sha = normalize_sha256(self.sha256)
        assert normalized_sha is not None
        object.__setattr__(self, "sha256", normalized_sha)
        if not isinstance(self.code, str) or not self.code.strip() or len(self.code) > 64:
            raise DomainValidationError("code 长度必须在 1 到 64 之间")
        object.__setattr__(self, "code", self.code.strip())
        for name in (
            "collection_task_id",
            "batch_id",
            "upload_session_id",
            "retention_policy_id",
            "duplicate_of_asset_id",
        ):
            object.__setattr__(self, name, _optional_uuid(getattr(self, name), name))
        object.__setattr__(self, "ingested_at", _aware(self.ingested_at, "ingested_at"))
        for name in (
            "captured_at",
            "verified_at",
            "archived_at",
            "tombstoned_at",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, name, _optional_aware(getattr(self, name), name))
        object.__setattr__(self, "device_ids", _uuid_tuple(self.device_ids, "device_ids"))
        object.__setattr__(self, "sensor_ids", _uuid_tuple(self.sensor_ids, "sensor_ids"))
        codes = tuple(self.quarantine_codes)
        if len(set(codes)) != len(codes):
            raise DomainValidationError("quarantine_codes 不得重复")
        object.__setattr__(self, "quarantine_codes", codes)
        if self.status is RawAssetStatus.QUARANTINED:
            if not codes:
                raise DomainValidationError("QUARANTINED 资产必须记录 quarantine_codes")
            if not self.quarantine_reason:
                raise DomainValidationError("QUARANTINED 资产必须记录 quarantine_reason")
        object.__setattr__(
            self,
            "raw_metadata",
            canonical_json(self.raw_metadata, "raw_metadata"),
        )
        object.__setattr__(self, "version", _positive(self.version, "version"))

    def assert_content_immutable(self, updated: RawAsset) -> None:
        """内容字段创建后不可变；供后续状态动作使用。"""
        for name in (
            "asset_id",
            "project_id",
            "source_type",
            "object_key",
            "size_bytes",
            "content_type",
            "format",
            "sha256",
            "upload_session_id",
        ):
            if getattr(self, name) != getattr(updated, name):
                raise ResourceImmutableError(f"资产内容字段不可变：{name}")

    def with_status(self, status: RawAssetStatus | str, *, now: datetime) -> RawAsset:
        updated = replace(self, status=status, updated_at=now)
        self.assert_content_immutable(updated)
        return updated

    def mark_valid(self, *, now: datetime) -> RawAsset:
        """VALIDATING → VALID；记录校验时间并清空隔离信息。"""
        target = raw_asset_target_status(self.status, "validate-success")
        updated = replace(
            self,
            status=target,
            verified_at=now,
            quarantine_reason=None,
            quarantine_codes=(),
            updated_at=now,
        )
        self.assert_content_immutable(updated)
        return updated

    def mark_quarantined(
        self,
        *,
        codes: Sequence[RawAssetQuarantineCode | str],
        reason: str,
        now: datetime,
    ) -> RawAsset:
        """VALIDATING → QUARANTINED；隔离码必须来自稳定枚举且不得重复。"""
        target = raw_asset_target_status(self.status, "validate-quarantine")
        normalized = _quarantine_codes(codes)
        if not isinstance(reason, str) or not reason.strip():
            raise DomainValidationError("隔离原因不能为空")
        cleaned_reason = reason.strip()
        if len(cleaned_reason) > 1000:
            raise DomainValidationError("隔离原因长度不得超过 1000")
        updated = replace(
            self,
            status=target,
            quarantine_codes=normalized,
            quarantine_reason=cleaned_reason,
            verified_at=None,
            updated_at=now,
        )
        self.assert_content_immutable(updated)
        return updated


@dataclass(frozen=True, slots=True)
class RawAssetLink:
    link_id: UUID
    project_id: UUID
    asset_id: UUID
    link_type: RawAssetLinkType | str
    link_status: RawAssetLinkStatus | str = RawAssetLinkStatus.PENDING_VERIFICATION
    collection_task_id: UUID | None = None
    batch_id: UUID | None = None
    upload_session_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "link_type", _enum(self.link_type, RawAssetLinkType, "link_type"))
        object.__setattr__(
            self,
            "link_status",
            _enum(self.link_status, RawAssetLinkStatus, "link_status"),
        )
        for name in (
            "collection_task_id",
            "batch_id",
            "upload_session_id",
            "created_by",
        ):
            object.__setattr__(self, name, _optional_uuid(getattr(self, name), name))
        if (
            self.collection_task_id is None
            and self.batch_id is None
            and self.upload_session_id is None
        ):
            raise DomainValidationError("使用关系至少需要一个来源引用")
        if self.batch_id is not None and self.collection_task_id is None:
            raise DomainValidationError("batch_id 必须与 collection_task_id 同时提供")
        object.__setattr__(self, "created_at", _optional_aware(self.created_at, "created_at"))


def _content_type(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("content_type 必须是非空字符串")
    content_type = value.strip().lower()
    if len(content_type) > CONTENT_TYPE_MAX_LENGTH:
        raise DomainValidationError(f"content_type 长度不得超过 {CONTENT_TYPE_MAX_LENGTH} 个字符")
    return content_type


def _enum(value: object, enum_type: type, field_name: str) -> object:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc


def _uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple | set | frozenset):
        raise DomainValidationError(f"{field_name} 必须是 UUID 列表")
    result: list[UUID] = []
    seen: set[UUID] = set()
    for item in value:
        if not isinstance(item, UUID):
            raise DomainValidationError(f"{field_name} 只能包含 UUID")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _quarantine_codes(
    values: Sequence[RawAssetQuarantineCode | str],
) -> tuple[str, ...]:
    if not isinstance(values, list | tuple):
        raise DomainValidationError("quarantine_codes 必须是列表")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        try:
            value = RawAssetQuarantineCode(item).value
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("quarantine_codes 含未知稳定码") from exc
        if value in seen:
            raise DomainValidationError("quarantine_codes 不得重复")
        seen.add(value)
        result.append(value)
    if not result:
        raise DomainValidationError("quarantine_codes 不能为空")
    return tuple(result)
