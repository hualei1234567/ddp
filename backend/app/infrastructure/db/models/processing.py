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
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.enums import (
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.steps import (
    STEP_ERROR_CODE_MAX_LENGTH,
    STEP_ERROR_SUMMARY_MAX_LENGTH,
    STEP_LOG_MAX_LENGTH,
)
from app.infrastructure.db.base import Base

_STATUS_VALUES = ", ".join(repr(item.value) for item in ProcessingRunStatus)
_STAGE_VALUES = ", ".join(repr(item.value) for item in ProcessingStage)
_STEP_STATUS_VALUES = ", ".join(repr(item.value) for item in ProcessingStepStatus)
_ARTIFACT_KIND_VALUES = ", ".join(repr(item.value) for item in ProcessingArtifactKind)
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
    #: N4-006：失败收口时间。为 NULL 表示 FAILED 仍可被 Worker 重试；
    #: 非 NULL 表示已保留错误原因地收口，不再允许任何出边。
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class ProcessingRunStepModel(Base):
    """单个阶段的一次执行尝试（N4-002）。

    ``(run_id, stage, attempt)`` 唯一：重试会增加 ``attempt``，历史失败行
    保留可查，不会被覆盖。数据库 CHECK 与领域不变量一一对应，绕过用例
    直接插行也写不出"跳过却没有原因""失败却没有错误码"的记录。
    """

    __tablename__ = "processing_run_steps"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("processing_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ProcessingStepStatus.RUNNING.value,
        server_default=text("'RUNNING'"),
    )
    log_summary: Mapped[str | None] = mapped_column(String(STEP_LOG_MAX_LENGTH), nullable=True)
    error_code: Mapped[str | None] = mapped_column(
        String(STEP_ERROR_CODE_MAX_LENGTH),
        nullable=True,
    )
    error_summary: Mapped[str | None] = mapped_column(
        String(STEP_ERROR_SUMMARY_MAX_LENGTH),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
        UniqueConstraint(
            "run_id",
            "stage",
            "attempt",
            name="uq_processing_run_steps_run_stage_attempt",
        ),
        CheckConstraint(f"stage IN ({_STAGE_VALUES})", name="stage_allowed"),
        CheckConstraint(f"status IN ({_STEP_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "status <> 'RUNNING' OR finished_at IS NULL",
            name="running_without_finished_at",
        ),
        CheckConstraint(
            "status = 'RUNNING' OR finished_at IS NOT NULL",
            name="terminal_requires_finished_at",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="finished_after_started",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR (error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name="failed_requires_error",
        ),
        CheckConstraint(
            "status = 'FAILED' OR (error_code IS NULL AND error_summary IS NULL)",
            name="error_only_when_failed",
        ),
        CheckConstraint(
            "status <> 'SKIPPED' OR (log_summary IS NOT NULL AND length(log_summary) > 0)",
            name="skipped_requires_reason",
        ),
        Index(
            "ix_processing_run_steps_project_run_id",
            "project_id",
            "run_id",
            "id",
        ),
    )


class ProcessingArtifactModel(Base):
    """运行产物目录（V31-016）。

    ``output_refs`` 只有 key，无法回答"哪个阶段、哪次尝试、什么类型、多大、
    摘要是否一致"；本表是权威记录，``output_refs`` 退化为兼容投影。

    唯一键 ``(run_id, stage, attempt, kind, object_key)``：同一阶段同一次
    尝试对同一对象只登记一次；重试换 ``attempt``，历史产物保留可查。
    跨项目由 ``project_id`` 列 + 复合外键 ``(project_id, run_id)`` 强制，
    绕过应用层直接插行也写不出跨项目引用。

    ``ON DELETE CASCADE``：运行是产物的生命周期边界，删除运行即删除其
    产物记录（对象存储中的对象由保留策略处理，不在本表职责内）。
    """

    __tablename__ = "processing_artifacts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("processing_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 内容摘要；部分阶段产物无稳定摘要（仅引用）时为 NULL。
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 产物来源输入对象（``raw/<project>/<asset>``）；无单一来源时为 NULL。
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage",
            "attempt",
            "kind",
            "object_key",
            name="uq_processing_artifacts_run_stage_attempt_kind_key",
        ),
        CheckConstraint(f"stage IN ({_STAGE_VALUES})", name="stage_allowed"),
        CheckConstraint(
            f"kind IN ({_ARTIFACT_KIND_VALUES})",
            name="kind_allowed",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(
            "checksum_sha256 IS NULL OR length(checksum_sha256) = 64",
            name="checksum_sha256_length",
        ),
        Index(
            "ix_processing_artifacts_project_run_created_at",
            "project_id",
            "run_id",
            "created_at",
        ),
    )


__all__ = ["ProcessingArtifactModel", "ProcessingRunModel", "ProcessingRunStepModel"]
