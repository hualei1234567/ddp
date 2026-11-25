"""PRM 步级标注 ORM 模型（V52-020）。

一条轨迹一行：``step_actions``（轨迹步骤序列）、``marks``（已标注步骤）
与 ``fork_lineage``（分叉血缘）都走 JSON 列——MEM-DEC-002：偏好与步级
结论用 JSON 约定值表达，不新增标签类型，也不把"每步一行"的展开写进
关系表（分叉重写会整段替换，行级展开反而让"哪些步是重写产物"无法回答）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_catalog import AnnotationAuditMixin

_JSON_TYPE = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class PrmAnnotationModel(Base, AnnotationAuditMixin):
    """PRM 步级标注聚合行（trajectory 粒度）。"""

    __tablename__ = "prm_annotations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # trajectory_id 是标注侧的稳定业务键（AGENT_TRACE 载体里的轨迹标识），
    # 同一轨迹在项目内只允许一份标注；分叉轨迹 id 由调用方分配并必须唯一。
    trajectory_id: Mapped[str] = mapped_column(String(200), nullable=False)
    task_goal: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    step_actions: Mapped[object] = mapped_column(_JSON_TYPE, nullable=False)
    marks: Mapped[object] = mapped_column(_JSON_TYPE, nullable=False)
    fork_lineage: Mapped[object | None] = mapped_column(_JSON_TYPE, nullable=True)
    # 乐观锁版本冗余计数：与 AnnotationAuditMixin.version 一致，供 API If-Match。
    step_count: Mapped[int] = mapped_column(Integer, nullable=False)
    marked_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_prm_annotations_project_id_id"),
        # 同一项目内同一轨迹只有一份步级标注（分叉轨迹的派生 id 不冲突）。
        UniqueConstraint("project_id", "trajectory_id", name="uq_prm_annotations_trajectory"),
        ForeignKeyConstraint(
            ["project_id", "created_by"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_prm_annotations_project_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trajectory_id) > 0", name="trajectory_id_non_empty"),
        CheckConstraint("step_count >= 1 AND step_count <= 512", name="step_count_bounds"),
        CheckConstraint(
            "marked_count >= 0 AND marked_count <= step_count", name="marked_count_bounds"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_prm_annotations_project_trajectory", "project_id", "trajectory_id"),
    )
