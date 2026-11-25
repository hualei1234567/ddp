"""阶段 3 任务、批次、分配与分配事件 ORM 模型。"""

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

from app.domain.annotation.enums import (
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationTaskStatus,
    AssignmentMode,
    AssignmentRole,
    TaskPriority,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")

#: 技能标签名最大长度（与领域常量 TAG_NAME_MAX_LENGTH 对齐，迁移 0080 同口径）
_SKILL_TAG_NAME_MAX = 64
#: 技能标签分类最大长度
_SKILL_TAG_CATEGORY_MAX = 64


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class AnnotationTask(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_tasks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_set_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    gold_set_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    iaa_policy: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    quality_policy: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    assignment_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    target_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    target_episode_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 技能路由配置（V52-009，迁移 0080）：JSON 标签列表；NULL = 不路由。
    # 语义由领域层 AnnotationTask.required_skills 统一校验，这里只存取。
    required_skills: Mapped[list[object] | None] = mapped_column(
        _JSON_TYPE,
        nullable=True,
        default=None,
        server_default=text("NULL"),
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    progress: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    quality_summary: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    iaa_report_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_tasks_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_annotation_tasks_project_code"),
        ForeignKeyConstraint(
            ["project_id", "sample_set_id"],
            ["annotation_sample_sets.project_id", "annotation_sample_sets.id"],
            name="fk_annotation_tasks_project_sample_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_tasks_project_schema",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationTaskStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(
            f"priority IN ({_values(TaskPriority)})",
            name="priority_allowed",
        ),
        CheckConstraint(
            f"assignment_mode IN ({_values(AssignmentMode)})",
            name="assignment_mode_allowed",
        ),
        CheckConstraint(
            "length(schema_sha256) = 71 AND substr(schema_sha256, 1, 7) = 'sha256:'",
            name="schema_sha256_format",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("target_item_count >= 1", name="target_item_count_positive"),
        CheckConstraint("target_episode_count >= 1", name="target_episode_count_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_tasks_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index("ix_annotation_tasks_project_sample_set", "project_id", "sample_set_id"),
        Index("ix_annotation_tasks_project_schema", "project_id", "schema_id"),
    )


class AnnotationBatch(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_batches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stratum: Mapped[str | None] = mapped_column(String(100), nullable=True)
    item_refs: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    planned_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    assignee_ids: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    reviewer_pool_ids: Mapped[list[str]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_summary: Mapped[dict[str, object]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_batches_project_id_id"),
        UniqueConstraint(
            "project_id",
            "task_id",
            "id",
            name="uq_annotation_batches_project_task_id",
        ),
        UniqueConstraint("project_id", "code", name="uq_annotation_batches_project_code"),
        UniqueConstraint(
            "project_id",
            "task_id",
            "sequence_no",
            name="uq_annotation_batches_project_task_sequence",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_batches_project_task",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationBatchStatus)})",
            name="status_allowed",
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("planned_item_count >= 0", name="planned_item_count_non_negative"),
        CheckConstraint("submitted_item_count >= 0", name="submitted_count_non_negative"),
        CheckConstraint("approved_item_count >= 0", name="approved_count_non_negative"),
        CheckConstraint("rejected_item_count >= 0", name="rejected_count_non_negative"),
        CheckConstraint("rework_count >= 0", name="rework_count_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_batches_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
        Index(
            "ix_annotation_batches_project_sequence_no",
            "project_id",
            "sequence_no",
        ),
    )


class AnnotationAssignment(Base, AnnotationAuditMixin):
    __tablename__ = "annotation_assignments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    assignee_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    assignment_role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    planned_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_annotation_assignments_project_id_id"),
        UniqueConstraint(
            "project_id",
            "batch_id",
            "assignee_id",
            "assignment_role",
            name="uq_annotation_assignments_batch_assignee_role",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_assignments_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id", "batch_id"],
            [
                "annotation_batches.project_id",
                "annotation_batches.task_id",
                "annotation_batches.id",
            ],
            name="fk_annotation_assignments_project_task_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "assignee_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_assignments_project_assignee",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"assignment_role IN ({_values(AssignmentRole)})",
            name="assignment_role_allowed",
        ),
        CheckConstraint(
            f"status IN ({_values(AnnotationAssignmentStatus)})",
            name="status_allowed",
        ),
        CheckConstraint("planned_item_count >= 0", name="planned_item_count_non_negative"),
        CheckConstraint("claimed_item_count >= 0", name="claimed_count_non_negative"),
        CheckConstraint("submitted_item_count >= 0", name="submitted_count_non_negative"),
        CheckConstraint("approved_item_count >= 0", name="approved_count_non_negative"),
        CheckConstraint("planned_minutes >= 0", name="planned_minutes_non_negative"),
        CheckConstraint(
            "actual_minutes IS NULL OR actual_minutes >= 0",
            name="actual_minutes_non_negative",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_annotation_assignments_project_assignee_status",
            "project_id",
            "assignee_id",
            "status",
        ),
        Index(
            "ix_annotation_assignments_project_batch_status",
            "project_id",
            "batch_id",
            "status",
        ),
        Index(
            "ix_annotation_assignments_project_task_status",
            "project_id",
            "task_id",
            "status",
        ),
    )


class AnnotationAssignmentEvent(Base):
    __tablename__ = "annotation_assignment_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    assignee_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    planned_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["annotation_assignments.project_id", "annotation_assignments.id"],
            name="fk_annotation_assignment_events_project_assignment",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(action) >= 1", name="action_non_empty"),
        CheckConstraint("planned_item_count >= 0", name="planned_count_non_negative"),
        CheckConstraint("submitted_item_count >= 0", name="submitted_count_non_negative"),
        CheckConstraint("approved_item_count >= 0", name="approved_count_non_negative"),
        CheckConstraint("minutes >= 0", name="minutes_non_negative"),
        Index(
            "ix_annotation_assignment_events_project_assignee_time",
            "project_id",
            "assignee_id",
            "occurred_at",
        ),
        Index(
            "ix_annotation_assignment_events_project_assignment",
            "project_id",
            "assignment_id",
        ),
    )


class AnnotatorSkillTag(Base):
    """标注员技能标签（V52-009，迁移 0080）。

    人员技能档案是**事实表**：一行 = 一名成员持有一个规范化标签。
    唯一约束 ``(project_id, user_id, tag_name)`` 防重复登记；``tag_name``
    由领域层在写入前规范化（小写、去空白），与任务侧
    ``required_skills`` 的匹配语义对齐。

    为什么带 ``project_id`` 并复合外键到 ``project_members``：技能标签是
    项目成员维度的事实（跨项目的技能档案治理各项目自治），且复用分配
    仓储既有的成员隔离口径——非成员不得有技能标签，数据库兜底。
    """

    __tablename__ = "annotator_skill_tags"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    annotator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tag_name: Mapped[str] = mapped_column(String(_SKILL_TAG_NAME_MAX), nullable=False)
    category: Mapped[str | None] = mapped_column(
        String(_SKILL_TAG_CATEGORY_MAX),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "annotator_id",
            "tag_name",
            name="uq_annotator_skill_tags_project_annotator_tag",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotator_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotator_skill_tags_project_annotator",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(tag_name) >= 1", name="tag_name_non_empty"),
        Index(
            "ix_annotator_skill_tags_project_annotator",
            "project_id",
            "annotator_id",
        ),
        Index(
            "ix_annotator_skill_tags_project_tag",
            "project_id",
            "tag_name",
        ),
    )
