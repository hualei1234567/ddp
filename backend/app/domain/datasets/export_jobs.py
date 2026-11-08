"""ExportJob 聚合。

状态机与 ``state-machines.v1.json`` 的 ExportJob 定义一致：
- QUEUED → RUNNING → SUCCEEDED/FAILED/PARTIAL/CANCELLED；
- FAILED 仅在 ``attempt < max_attempts`` 时允许 retry，且 retry 必须 attempt+1；
- PARTIAL 不是成功，终态不得升级为 SUCCEEDED；
- 取消后不得继续写产物或重试。

本模块只包含纯 Python 规则，不依赖 Web/ORM/队列/云 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.datasets.canonical import sha256_value
from app.domain.datasets.enums import (
    ExportFormat,
    ExportJobStatus,
    ExportVerificationStatus,
)
from app.domain.datasets.errors import (
    DomainValidationError,
    ExportConfigInvalidError,
    ExportFormatVersionUnsupportedError,
    ExportJobNotRetryableError,
    ExportTimeoutError,
    InvalidStateTransitionError,
)
from app.domain.datasets.export_validation import (
    EXPORT_FORMAT_VERSION_LE_ROBOT,
    IDEMPOTENCY_KEY_MAX_LENGTH,
    IDEMPOTENCY_KEY_MIN_LENGTH,
    coerce_format,
    config_payload,
    optional_non_negative,
    safe_output_prefix,
    text_limit,
)
from app.domain.datasets.state_machines import (
    allowed_export_actions,
    export_target,
)
from app.domain.datasets.validation import (
    aware_datetime,
    coerce_enum,
    non_negative_int,
    optional_aware_datetime,
    optional_sha256,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    text_value,
)
from app.domain.datasets.validation import (
    sha256_value as ensure_sha,
)


@dataclass(frozen=True, slots=True)
class ExportJob:
    export_job_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    format: ExportFormat | str
    exporter_id: str
    exporter_version: str
    mapping_version: str
    config: dict[str, object]
    idempotency_key: str
    requested_by: UUID
    output_prefix: str
    status: ExportJobStatus | str = ExportJobStatus.QUEUED
    format_version: str = EXPORT_FORMAT_VERSION_LE_ROBOT
    config_sha256: str | None = None
    artifact_count: int | None = None
    total_bytes: int | None = None
    artifact_manifest_key: str | None = None
    artifact_manifest_sha256: str | None = None
    aggregate_sha256: str | None = None
    attempt: int = 0
    max_attempts: int = 3
    timeout_seconds: int = 3600
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    log_ref: str | None = None
    verification_status: ExportVerificationStatus | str = (
        ExportVerificationStatus.UNVERIFIED
    )
    verification_evidence_ref: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("export_job_id", "project_id", "dataset_version_id", "requested_by"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "format",
            coerce_format(self.format),
        )
        if self.format_version != EXPORT_FORMAT_VERSION_LE_ROBOT:
            raise ExportFormatVersionUnsupportedError("仅支持 ddp.lerobot/1.0.0")
        for name, limit in (
            ("exporter_id", 100),
            ("exporter_version", 100),
            ("mapping_version", 100),
        ):
            object.__setattr__(self, name, text_value(getattr(self, name), name, max_length=limit))
        config = config_payload(self.config)
        object.__setattr__(self, "config", config)
        digest = sha256_value(config)
        if self.config_sha256 is not None and ensure_sha(
            self.config_sha256, "config_sha256"
        ) != digest:
            raise ExportConfigInvalidError("config_sha256 与配置内容不一致")
        object.__setattr__(self, "config_sha256", digest)
        key = text_value(
            self.idempotency_key,
            "idempotency_key",
            max_length=IDEMPOTENCY_KEY_MAX_LENGTH,
        )
        if len(key) < IDEMPOTENCY_KEY_MIN_LENGTH:
            raise DomainValidationError(
                f"idempotency_key 长度不得小于 {IDEMPOTENCY_KEY_MIN_LENGTH}"
            )
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "output_prefix", safe_output_prefix(self.output_prefix))
        object.__setattr__(self, "status", coerce_enum(self.status, ExportJobStatus, "status"))
        object.__setattr__(
            self,
            "verification_status",
            coerce_enum(
                self.verification_status,
                ExportVerificationStatus,
                "verification_status",
            ),
        )
        object.__setattr__(self, "attempt", non_negative_int(self.attempt, "attempt"))
        object.__setattr__(
            self,
            "max_attempts",
            positive_int(self.max_attempts, "max_attempts"),
        )
        if self.attempt > self.max_attempts:
            raise DomainValidationError("attempt 不得超过 max_attempts")
        object.__setattr__(
            self,
            "timeout_seconds",
            positive_int(self.timeout_seconds, "timeout_seconds"),
        )
        object.__setattr__(
            self,
            "artifact_count",
            optional_non_negative(self.artifact_count, "artifact_count"),
        )
        object.__setattr__(
            self,
            "total_bytes",
            optional_non_negative(self.total_bytes, "total_bytes"),
        )
        for name in (
            "artifact_manifest_key",
            "log_ref",
            "verification_evidence_ref",
            "error_code",
        ):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=text_limit(name)),
            )
        for name in ("artifact_manifest_sha256", "aggregate_sha256"):
            object.__setattr__(self, name, optional_sha256(getattr(self, name), name))
        for name in (
            "started_at",
            "finished_at",
            "cancel_requested_at",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "error_summary",
            optional_text(self.error_summary, "error_summary", max_length=2000),
        )
        for name in ("created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))
        self._assert_invariants()

    def _assert_invariants(self) -> None:
        status = ExportJobStatus(self.status)
        if status is ExportJobStatus.RUNNING:
            if self.started_at is None:
                raise DomainValidationError("RUNNING 导出任务必须记录 started_at")
            if self.attempt < 1:
                raise DomainValidationError("RUNNING 导出任务的 attempt 必须大于等于 1")
        if status is ExportJobStatus.SUCCEEDED:
            required = (
                self.finished_at,
                self.artifact_count,
                self.total_bytes,
                self.artifact_manifest_key,
                self.artifact_manifest_sha256,
                self.aggregate_sha256,
            )
            if any(value is None for value in required):
                raise DomainValidationError(
                    "SUCCEEDED 导出任务必须记录产物统计、清单与汇总 Hash"
                )
        if status in {ExportJobStatus.FAILED, ExportJobStatus.PARTIAL}:
            if self.finished_at is None:
                raise DomainValidationError(f"{status.value} 导出任务必须记录 finished_at")
            if self.error_code is None or self.error_summary is None:
                raise DomainValidationError(f"{status.value} 导出任务必须记录错误码与摘要")
        if status is ExportJobStatus.CANCELLED and self.finished_at is None:
            raise DomainValidationError("CANCELLED 导出任务必须记录 finished_at")

    @property
    def is_terminal(self) -> bool:
        return ExportJobStatus(self.status) in {
            ExportJobStatus.SUCCEEDED,
            ExportJobStatus.PARTIAL,
            ExportJobStatus.CANCELLED,
        }

    @property
    def can_retry(self) -> bool:
        return (
            ExportJobStatus(self.status) is ExportJobStatus.FAILED
            and self.attempt < self.max_attempts
        )

    @property
    def timeout_at(self) -> datetime | None:
        if self.started_at is None:
            return None
        return self.started_at + timedelta(seconds=self.timeout_seconds)

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_export_actions(ExportJobStatus(self.status))

    def start(self, now: datetime) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "worker-start")
        return replace(
            self,
            status=target,
            attempt=self.attempt or 1,
            started_at=now,
            finished_at=None,
            updated_at=now,
        )

    def succeed(
        self,
        *,
        now: datetime,
        artifact_count: int,
        total_bytes: int,
        artifact_manifest_key: str,
        artifact_manifest_sha256: str,
        aggregate_sha256: str,
        log_ref: str | None = None,
    ) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "worker-succeed")
        return replace(
            self,
            status=target,
            finished_at=now,
            updated_at=now,
            artifact_count=non_negative_int(artifact_count, "artifact_count"),
            total_bytes=non_negative_int(total_bytes, "total_bytes"),
            artifact_manifest_key=text_value(
                artifact_manifest_key,
                "artifact_manifest_key",
                max_length=1024,
            ),
            artifact_manifest_sha256=ensure_sha(
                artifact_manifest_sha256,
                "artifact_manifest_sha256",
            ),
            aggregate_sha256=ensure_sha(aggregate_sha256, "aggregate_sha256"),
            error_code=None,
            error_summary=None,
            log_ref=optional_text(log_ref, "log_ref", max_length=1024),
        )

    def fail(
        self,
        *,
        now: datetime,
        error_code: str,
        error_summary: str,
        log_ref: str | None = None,
    ) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "worker-fail")
        return replace(
            self,
            status=target,
            finished_at=now,
            updated_at=now,
            error_code=text_value(error_code, "error_code", max_length=100),
            error_summary=text_value(error_summary, "error_summary", max_length=2000),
            log_ref=optional_text(log_ref, "log_ref", max_length=1024),
        )

    def partial(
        self,
        *,
        now: datetime,
        error_code: str,
        error_summary: str,
        artifact_count: int | None = None,
        total_bytes: int | None = None,
        log_ref: str | None = None,
    ) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "worker-partial")
        return replace(
            self,
            status=target,
            finished_at=now,
            updated_at=now,
            artifact_count=(
                non_negative_int(artifact_count, "artifact_count")
                if artifact_count is not None
                else self.artifact_count
            ),
            total_bytes=(
                non_negative_int(total_bytes, "total_bytes")
                if total_bytes is not None
                else self.total_bytes
            ),
            error_code=text_value(error_code, "error_code", max_length=100),
            error_summary=text_value(error_summary, "error_summary", max_length=2000),
            log_ref=optional_text(log_ref, "log_ref", max_length=1024),
        )

    def fail_timeout(self, now: datetime) -> ExportJob:
        if ExportJobStatus(self.status) is not ExportJobStatus.RUNNING:
            raise InvalidStateTransitionError("导出任务", self.status.value, "job-timeout")
        deadline = self.timeout_at
        if deadline is None or now < deadline:
            raise ExportTimeoutError("导出任务尚未超过 timeout_seconds")
        return self.fail(
            now=now,
            error_code=ExportTimeoutError.code,
            error_summary="导出任务执行超时",
        )

    def retry(self, now: datetime) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "retry")
        if not self.can_retry:
            raise ExportJobNotRetryableError("导出任务不可重试或已达最大尝试次数")
        cleared = {
            "artifact_manifest_key": None,
            "artifact_manifest_sha256": None,
            "aggregate_sha256": None,
        }
        if self.artifact_count is None:
            cleared["artifact_count"] = None
            cleared["total_bytes"] = None
        return replace(
            self,
            status=target,
            attempt=self.attempt + 1,
            started_at=None,
            finished_at=None,
            cancel_requested_at=None,
            error_code=None,
            error_summary=None,
            log_ref=None,
            updated_at=now,
            **cleared,
        )

    def cancel(self, now: datetime) -> ExportJob:
        target = export_target(ExportJobStatus(self.status), "cancel")
        return replace(
            self,
            status=target,
            cancel_requested_at=now,
            finished_at=now,
            updated_at=now,
        )

    def matches_idempotent_request(self, other: ExportJob) -> bool:
        """同一幂等键必须对应同一版本、格式、Exporter 与配置。"""
        return (
            self.dataset_version_id == other.dataset_version_id
            and self.format == other.format
            and self.format_version == other.format_version
            and self.exporter_id == other.exporter_id
            and self.exporter_version == other.exporter_version
            and self.mapping_version == other.mapping_version
            and self.config == other.config
            and self.config_sha256 == other.config_sha256
            and self.timeout_seconds == other.timeout_seconds
            and self.max_attempts == other.max_attempts
        )


def new_export_job(
    *,
    export_job_id: UUID,
    project_id: UUID,
    dataset_version_id: UUID,
    format: object,
    exporter_id: str,
    exporter_version: str,
    mapping_version: str,
    config: object,
    idempotency_key: str,
    requested_by: UUID,
    output_prefix: str,
    now: datetime,
    timeout_seconds: int,
    max_attempts: int,
    format_version: str = EXPORT_FORMAT_VERSION_LE_ROBOT,
) -> ExportJob:
    return ExportJob(
        export_job_id=export_job_id,
        project_id=project_id,
        dataset_version_id=dataset_version_id,
        format=format,
        format_version=format_version,
        exporter_id=exporter_id,
        exporter_version=exporter_version,
        mapping_version=mapping_version,
        config=config_payload(config),
        idempotency_key=idempotency_key,
        requested_by=requested_by,
        output_prefix=output_prefix,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        created_at=now,
        updated_at=now,
        created_by=requested_by,
        updated_by=requested_by,
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EXPORT_FORMAT_VERSION_LE_ROBOT",
    "ExportJob",
    "new_export_job",
    "utcnow",
]
