"""ProcessingRun ORM 模型（N4-001）。

只存元数据与对象存储引用，不存大对象本体。数据库 CHECK 是应用层不变量
的兜底：即使有人绕过用例直接插行，也不能写出非法状态、非法阶段或
``FAILED`` 却没有错误信息。

索引 ``(project_id, status, created_at)`` 对应"按项目 + 状态看最近运行"的
主查询；项目隔离由 ``project_id`` 前缀天然保证。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.processing.enums import ProcessingRunStatus, ProcessingStage
from app.infrastructure.db.base import Base

_STATUS_VALUES = ", ".join(repr(item.value) for item in ProcessingRunStatus)
_STAGE_VALUES = ", ".join(repr(item.value) for item in ProcessingStage)
_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class ProcessingRunModel(Base):
    __tablename__ = "processing_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_refs: Mapped[list[str]] = mapped_column(_JSON_TYPE, nullable=False)
    output_refs: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProcessingRunStatus.CREATED.value,
        server_default=text("'CREATED'"),
    )
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint(
            f"stage IS NULL OR stage IN ({_STAGE_VALUES})",
            name="stage_allowed",
        ),
        CheckConstraint("attempt >= 0", name="attempt_non_negative"),
        CheckConstraint("retry_count >= 0", name="retry_count_non_negative"),
        CheckConstraint("retry_count <= attempt", name="retry_within_attempt"),
        CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR stage IS NOT NULL",
            name="active_requires_stage",
        ),
        CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR started_at IS NOT NULL",
            name="active_requires_started_at",
        ),
        CheckConstraint(
            "status NOT IN ('RUNNING', 'SUCCEEDED', 'FAILED') OR attempt >= 1",
            name="active_requires_attempt",
        ),
        CheckConstraint(
            "status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED') OR finished_at IS NOT NULL",
            name="finished_requires_finished_at",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') OR finished_at IS NULL",
            name="unfinished_without_finished_at",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR (error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name="failed_requires_error",
        ),
        CheckConstraint(
            "status = 'FAILED' OR (error_code IS NULL AND error_summary IS NULL)",
            name="error_only_when_failed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_processing_runs_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
    )


__all__ = ["ProcessingRunModel"]
