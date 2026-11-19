"""采集会话 ORM 模型（N9-001）。

为什么单独成文件而不是塞进 ``collection.py``：后者已承载任务/批次/审计/
outbox/幂等五个模型、接近 500 行评审阈值；会话是独立聚合，拆开后两个文件
都保持可审阅。公共审计字段仍复用 ``ProjectScopedAuditMixin``，不另写一套
"项目隔离 + 乐观锁 + 四人字段"。

``task_id`` / ``batch_id`` 使用 ``(project_id, id)`` 复合外键：会话可以绑定
任务或批次，但**永不跨项目**。``robot_id`` / ``location_id`` 同样带项目维度。
``operator_id`` 只引用全局 ``users.id``，成员资格由应用层校验（与任务
``assigned_to`` 一致）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.collection.enums import CaptureSessionStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import ProjectScopedAuditMixin

_STATUS_VALUES = ", ".join(repr(item.value) for item in CaptureSessionStatus)


class CaptureSessionModel(Base, ProjectScopedAuditMixin):
    __tablename__ = "capture_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    operator_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    robot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    location_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_capture_sessions_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_capture_sessions_project_code"),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_capture_sessions_project_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "batch_id"],
            ["collection_batches.project_id", "collection_batches.id"],
            name="fk_capture_sessions_project_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_capture_sessions_project_robot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "location_id"],
            ["locations.project_id", "locations.id"],
            name="fk_capture_sessions_project_location",
            ondelete="NO ACTION",
        ),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="session_range_valid",
        ),
        Index(
            "ix_capture_sessions_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_capture_sessions_project_task_created_at",
            "project_id",
            "task_id",
            "created_at",
        ),
    )


__all__ = ["CaptureSessionModel"]
