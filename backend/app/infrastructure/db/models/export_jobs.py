"""ExportJob ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.datasets.enums import (
    ExportFormat,
    ExportJobStatus,
    ExportVerificationStatus,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


def _sha_check(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(_SHA.format(column=column), name=name)


class ExportJobModel(Base):
    __tablename__ = "export_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    format_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exporter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    exporter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    output_prefix: Mapped[str] = mapped_column(String(1024), nullable=False)
    artifact_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    artifact_manifest_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_manifest_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    aggregate_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3600, server_default=text("3600")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    log_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ExportVerificationStatus.UNVERIFIED.value,
        server_default=text("'UNVERIFIED'"),
    )
    verification_evidence_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_export_jobs_project_id_id"),
        UniqueConstraint(
            "project_id",
            "requested_by",
            "idempotency_key",
            name="uq_export_jobs_project_user_idempotency",
        ),
        ForeignKeyConstraint(
            ["project_id", "dataset_version_id"],
            ["dataset_versions.project_id", "dataset_versions.id"],
            name="fk_export_jobs_project_dataset_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_values(ExportJobStatus)})", name="status_allowed"),
        CheckConstraint(f"format IN ({_values(ExportFormat)})", name="format_allowed"),
        CheckConstraint(
            f"verification_status IN ({_values(ExportVerificationStatus)})",
            name="verification_status_allowed",
        ),
        CheckConstraint("length(format_version) >= 1", name="format_version_non_empty"),
        CheckConstraint("length(exporter_id) >= 1", name="exporter_id_non_empty"),
        CheckConstraint("length(exporter_version) >= 1", name="exporter_version_non_empty"),
        CheckConstraint("length(mapping_version) >= 1", name="mapping_version_non_empty"),
        CheckConstraint(
            "length(idempotency_key) >= 8 AND length(idempotency_key) <= 128",
            name="idempotency_key_length",
        ),
        CheckConstraint("length(output_prefix) >= 1", name="output_prefix_non_empty"),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        CheckConstraint("attempt <= max_attempts", name="attempt_within_max"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("timeout_seconds >= 1", name="timeout_positive"),
        CheckConstraint(
            "artifact_count IS NULL OR artifact_count >= 0",
            name="artifact_count_non_negative",
        ),
        CheckConstraint(
            "total_bytes IS NULL OR total_bytes >= 0",
            name="total_bytes_non_negative",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        _sha_check("config_sha256", "config_sha256_format"),
        CheckConstraint(
            "artifact_manifest_sha256 IS NULL OR ("
            "length(artifact_manifest_sha256) = 71 "
            "AND substr(artifact_manifest_sha256, 1, 7) = 'sha256:')",
            name="artifact_manifest_sha256_format",
        ),
        CheckConstraint(
            "aggregate_sha256 IS NULL OR ("
            "length(aggregate_sha256) = 71 "
            "AND substr(aggregate_sha256, 1, 7) = 'sha256:')",
            name="aggregate_sha256_format",
        ),
        CheckConstraint(
            "status <> 'RUNNING' OR (started_at IS NOT NULL AND attempt >= 1)",
            name="running_requires_started",
        ),
        CheckConstraint(
            "status NOT IN ('SUCCEEDED') OR ("
            "finished_at IS NOT NULL AND artifact_count IS NOT NULL "
            "AND total_bytes IS NOT NULL AND artifact_manifest_key IS NOT NULL "
            "AND artifact_manifest_sha256 IS NOT NULL AND aggregate_sha256 IS NOT NULL)",
            name="succeeded_requires_artifacts",
        ),
        CheckConstraint(
            "status NOT IN ('FAILED', 'PARTIAL') OR ("
            "finished_at IS NOT NULL AND error_code IS NOT NULL "
            "AND error_summary IS NOT NULL)",
            name="failure_requires_error",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR finished_at IS NOT NULL",
            name="cancelled_requires_finished",
        ),
        Index("ix_export_jobs_project_status_created_at", "project_id", "status", "created_at"),
        Index(
            "ix_export_jobs_project_version_created_at",
            "project_id",
            "dataset_version_id",
            "created_at",
        ),
    )
