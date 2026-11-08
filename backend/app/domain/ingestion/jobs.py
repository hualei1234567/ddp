"""IngestionJob 聚合。

Job 的状态只能经具名动作迁移。失败必须记录稳定错误码、摘要与结束时间；
``FAILED`` 只有在 ``attempt < max_attempts`` 时允许重试，重试后进入 ``QUEUED``，
不存在“失败后直接成功”或“RUNNING 直接回到 QUEUED”的路径。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.ingestion.enums import (
    IngestionJobAction,
    IngestionJobStatus,
    IngestionJobType,
)
from app.domain.ingestion.errors import (
    DomainValidationError,
    InvalidStateTransitionError,
    JobNotRetryableError,
    JobTimeoutError,
)
from app.domain.ingestion.job_state_machines import ingestion_job_target_status
from app.domain.ingestion.validation import normalize_sha256

IDEMPOTENCY_KEY_MAX_LENGTH = 128
ERROR_CODE_MAX_LENGTH = 64
SUMMARY_MAX_LENGTH = 2000
REF_MAX_LENGTH = 1024
VERSION_TEXT_MAX_LENGTH = 128
JSON_MAX_BYTES = 64 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区（UTC）")
    return value


def _optional_aware(value: datetime | None, field_name: str) -> datetime | None:
    return None if value is None else _aware(value, field_name)


def _positive(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def _optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} 必须是非空字符串或 null")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field_name} 长度不得超过 {max_length}")
    return cleaned


def _required_text(value: object, field_name: str, *, max_length: int) -> str:
    cleaned = _optional_text(value, field_name, max_length=max_length)
    if cleaned is None:
        raise DomainValidationError(f"{field_name} 不能为空")
    return cleaned


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


def _json_mapping(value: object, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    copied = {str(key): item for key, item in value.items()}
    try:
        encoded = json.dumps(copied, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 对象") from exc
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后不得超过 {JSON_MAX_BYTES} 字节")
    return copied


def _json_list(value: object, field_name: str) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise DomainValidationError(f"{field_name} 必须是对象数组")
    items: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DomainValidationError(f"{field_name} 只能包含对象")
        items.append(_json_mapping(item, field_name))
    try:
        encoded = json.dumps(items, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 数组") from exc
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后不得超过 {JSON_MAX_BYTES} 字节")
    return tuple(items)


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: UUID
    project_id: UUID
    job_type: IngestionJobType | str
    timeout_sec: int
    raw_asset_ids: tuple[UUID, ...] = ()
    raw_asset_id: UUID | None = None
    input_manifest_key: str | None = None
    idempotency_key: str | None = None
    status: IngestionJobStatus | str = IngestionJobStatus.QUEUED
    attempt: int = 1
    max_attempts: int = 3
    pipeline_id: str | None = None
    pipeline_version: str | None = None
    parser_id: str | None = None
    parser_version: str | None = None
    config_hash: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    output_refs: tuple[Mapping[str, object], ...] = ()
    log_ref: str | None = None
    metrics: Mapping[str, object] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, UUID) or not isinstance(self.project_id, UUID):
            raise DomainValidationError("job_id/project_id 必须是 UUID")
        object.__setattr__(self, "job_type", _enum(self.job_type, IngestionJobType, "job_type"))
        object.__setattr__(self, "status", _enum(self.status, IngestionJobStatus, "status"))
        object.__setattr__(self, "timeout_sec", _positive(self.timeout_sec, "timeout_sec"))
        object.__setattr__(
            self,
            "raw_asset_ids",
            _uuid_tuple(self.raw_asset_ids, "raw_asset_ids"),
        )
        if len(self.raw_asset_ids) > 64:
            raise DomainValidationError("raw_asset_ids 不得超过 64 个")
        if self.raw_asset_id is not None:
            if not isinstance(self.raw_asset_id, UUID):
                raise DomainValidationError("raw_asset_id 必须是 UUID 或 null")
            if self.raw_asset_ids and self.raw_asset_id not in self.raw_asset_ids:
                raise DomainValidationError("raw_asset_id 必须属于 raw_asset_ids")
            if not self.raw_asset_ids:
                object.__setattr__(self, "raw_asset_ids", (self.raw_asset_id,))
        elif self.raw_asset_ids:
            object.__setattr__(self, "raw_asset_id", self.raw_asset_ids[0])
        object.__setattr__(
            self,
            "input_manifest_key",
            _optional_text(
                self.input_manifest_key,
                "input_manifest_key",
                max_length=REF_MAX_LENGTH,
            ),
        )
        if not self.raw_asset_ids and self.input_manifest_key is None:
            raise DomainValidationError("接入任务至少需要一个 raw_asset 或 input_manifest_key")
        object.__setattr__(
            self,
            "idempotency_key",
            _optional_text(
                self.idempotency_key,
                "idempotency_key",
                max_length=IDEMPOTENCY_KEY_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "attempt", _positive(self.attempt, "attempt"))
        object.__setattr__(self, "max_attempts", _positive(self.max_attempts, "max_attempts"))
        if self.attempt > self.max_attempts:
            raise DomainValidationError("attempt 不得超过 max_attempts")
        for name, limit in (
            ("pipeline_id", VERSION_TEXT_MAX_LENGTH),
            ("pipeline_version", 64),
            ("parser_id", VERSION_TEXT_MAX_LENGTH),
            ("parser_version", 64),
        ):
            object.__setattr__(
                self,
                name,
                _optional_text(getattr(self, name), name, max_length=limit),
            )
        object.__setattr__(
            self,
            "config_hash",
            normalize_sha256(self.config_hash, required=False),
        )
        for name in ("started_at", "finished_at", "created_at", "updated_at"):
            object.__setattr__(self, name, _optional_aware(getattr(self, name), name))
        for name, limit in (
            ("error_code", ERROR_CODE_MAX_LENGTH),
            ("error_summary", SUMMARY_MAX_LENGTH),
            ("log_ref", REF_MAX_LENGTH),
        ):
            object.__setattr__(
                self,
                name,
                _optional_text(getattr(self, name), name, max_length=limit),
            )
        object.__setattr__(self, "output_refs", _json_list(self.output_refs, "output_refs"))
        object.__setattr__(self, "metrics", _json_mapping(self.metrics, "metrics"))
        object.__setattr__(self, "version", _positive(self.version, "version"))
        if self.status is IngestionJobStatus.RUNNING and self.started_at is None:
            raise DomainValidationError("RUNNING 任务必须记录 started_at")
        if self.status is IngestionJobStatus.SUCCEEDED and self.finished_at is None:
            raise DomainValidationError("SUCCEEDED 任务必须记录 finished_at")
        if self.status is IngestionJobStatus.CANCELLED and self.finished_at is None:
            raise DomainValidationError("CANCELLED 任务必须记录 finished_at")
        if self.status is IngestionJobStatus.FAILED:
            if self.error_code is None or self.error_summary is None:
                raise DomainValidationError("FAILED 任务必须记录 error_code 与 error_summary")
            if self.finished_at is None:
                raise DomainValidationError("FAILED 任务必须记录 finished_at")

    @property
    def is_terminal(self) -> bool:
        return self.status in {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.CANCELLED}

    @property
    def can_retry(self) -> bool:
        return self.status is IngestionJobStatus.FAILED and self.attempt < self.max_attempts

    @property
    def timeout_at(self) -> datetime | None:
        if self.started_at is None:
            return None
        return self.started_at + timedelta(seconds=self.timeout_sec)

    def start(self, now: datetime) -> IngestionJob:
        target = ingestion_job_target_status(self.status, IngestionJobAction.WORKER_START.value)
        return replace(self, status=target, started_at=now, updated_at=now)

    def succeed(
        self,
        *,
        now: datetime,
        output_refs: Sequence[Mapping[str, object]] | None = None,
        log_ref: str | None = None,
        metrics: Mapping[str, object] | None = None,
    ) -> IngestionJob:
        target = ingestion_job_target_status(self.status, IngestionJobAction.WORKER_SUCCEED.value)
        return replace(
            self,
            status=target,
            finished_at=now,
            updated_at=now,
            error_code=None,
            error_summary=None,
            output_refs=tuple(output_refs) if output_refs is not None else self.output_refs,
            log_ref=log_ref if log_ref is not None else self.log_ref,
            metrics=dict(metrics) if metrics is not None else self.metrics,
        )

    def fail(
        self,
        *,
        error_code: str,
        error_summary: str,
        now: datetime,
        output_refs: Sequence[Mapping[str, object]] | None = None,
        log_ref: str | None = None,
    ) -> IngestionJob:
        target = ingestion_job_target_status(self.status, IngestionJobAction.WORKER_FAIL.value)
        return replace(
            self,
            status=target,
            finished_at=now,
            updated_at=now,
            error_code=_required_text(error_code, "error_code", max_length=ERROR_CODE_MAX_LENGTH),
            error_summary=_required_text(
                error_summary,
                "error_summary",
                max_length=SUMMARY_MAX_LENGTH,
            ),
            output_refs=tuple(output_refs) if output_refs is not None else self.output_refs,
            log_ref=log_ref if log_ref is not None else self.log_ref,
        )

    def fail_timeout(self, now: datetime) -> IngestionJob:
        if self.status is not IngestionJobStatus.RUNNING:
            raise InvalidStateTransitionError("接入任务", self.status.value, "job-timeout")
        deadline = self.timeout_at
        if deadline is None or now < deadline:
            raise JobTimeoutError("任务尚未超过 timeout_sec，不能按超时失败")
        return self.fail(
            error_code=JobTimeoutError.code,
            error_summary="任务执行超时",
            now=now,
        )

    def retry(self, now: datetime) -> IngestionJob:
        target = ingestion_job_target_status(self.status, IngestionJobAction.RETRY.value)
        if not self.can_retry:
            raise JobNotRetryableError("任务已达到最大重试次数，不能重试")
        return replace(
            self,
            status=target,
            attempt=self.attempt + 1,
            started_at=None,
            finished_at=None,
            error_code=None,
            error_summary=None,
            updated_at=now,
        )

    def request_cancel(self, now: datetime) -> IngestionJob:
        target = ingestion_job_target_status(self.status, IngestionJobAction.CANCEL.value)
        return replace(self, status=target, updated_at=now)

    def confirm_cancel(self, now: datetime) -> IngestionJob:
        target = ingestion_job_target_status(
            self.status,
            IngestionJobAction.WORKER_CANCELLED.value,
        )
        return replace(self, status=target, finished_at=now, updated_at=now)

    def matches_idempotent_request(self, other: IngestionJob) -> bool:
        """同一幂等键必须对应相同输入、配置与上限，否则视为键复用。"""
        return (
            self.job_type == other.job_type
            and self.raw_asset_ids == other.raw_asset_ids
            and self.input_manifest_key == other.input_manifest_key
            and self.pipeline_id == other.pipeline_id
            and self.pipeline_version == other.pipeline_version
            and self.parser_id == other.parser_id
            and self.parser_version == other.parser_version
            and self.config_hash == other.config_hash
            and self.timeout_sec == other.timeout_sec
            and self.max_attempts == other.max_attempts
        )


def _enum(value: object, enum_type: type, field_name: str) -> object:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc
