"""IngestionJob ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
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

from app.domain.ingestion.enums import IngestionJobStatus, IngestionJobType
from app.infrastructure.db.base import Base

_JOB_TYPE_VALUES = ", ".join(repr(item.value) for item in IngestionJobType)
_JOB_STATUS_VALUES = ", ".join(repr(item.value) for item in IngestionJobStatus)
_SHA256_CHECK = "length(config_hash) = 71 AND substr(config_hash, 1, 7) = 'sha256:'"
_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class IngestionJobModel(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_asset_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    raw_asset_ids: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    input_manifest_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    output_refs: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    log_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    metrics: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
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
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    updated_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_ingestion_jobs_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "raw_asset_id"],
            ["raw_assets.project_id", "raw_assets.id"],
            name="fk_ingestion_jobs_project_raw_asset",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"job_type IN ({_JOB_TYPE_VALUES})", name="job_type_allowed"),
        CheckConstraint(f"status IN ({_JOB_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempt <= max_attempts", name="attempt_within_max"),
        CheckConstraint("timeout_sec > 0", name="timeout_positive"),
        CheckConstraint(
            "raw_asset_id IS NOT NULL OR input_manifest_key IS NOT NULL",
            name="input_present",
        ),
        CheckConstraint(
            "config_hash IS NULL OR (" + _SHA256_CHECK + ")",
            name="config_hash_format",
        ),
        CheckConstraint(
            "status <> 'RUNNING' OR started_at IS NOT NULL",
            name="running_requires_started_at",
        ),
        CheckConstraint(
            "status <> 'SUCCEEDED' OR finished_at IS NOT NULL",
            name="succeeded_requires_finished_at",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR ("
            "error_code IS NOT NULL AND error_summary IS NOT NULL AND finished_at IS NOT NULL"
            ")",
            name="failed_requires_error",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_ingestion_jobs_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index("ix_ingestion_jobs_project_raw_asset", "project_id", "raw_asset_id"),
        Index(
            "ix_ingestion_jobs_project_status_attempt",
            "project_id",
            "status",
            "attempt",
        ),
        Index(
            "uq_ingestion_jobs_idempotency",
            "project_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )
