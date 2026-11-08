"""阶段 2 采集任务/批次、审计、outbox 与幂等 ORM 模型。

所有业务表强制 ``project_id``；父引用使用 ``(project_id, id)`` 复合外键，
跨项目引用由数据库拒绝。状态列使用 ``varchar + CHECK``，并由迁移安装的
触发器校验合法迁移，防止后台脚本直写绕过领域动作。
"""

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
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.collection.enums import (
    CollectionBatchStatus,
    CollectionTaskStatus,
    TaskPriority,
)
from app.infrastructure.db.base import Base

_TASK_STATUS_VALUES = ", ".join(repr(item.value) for item in CollectionTaskStatus)
_BATCH_STATUS_VALUES = ", ".join(repr(item.value) for item in CollectionBatchStatus)
_PRIORITY_VALUES = ", ".join(repr(item.value) for item in TaskPriority)
_OUTBOX_STATUS_VALUES = "'PENDING', 'PUBLISHED', 'FAILED'"


def _enum_check(column: str, values: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({values})", name=name)


class ProjectScopedAuditMixin:
    """采集域公共字段：项目隔离、乐观锁与审计。"""

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
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


class CollectionTask(Base, ProjectScopedAuditMixin):
    __tablename__ = "collection_tasks"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    plan_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sop_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sop_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action_spec_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action_spec_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    assigned_to: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    assigned_robot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    location_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    scheduled_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    scheduled_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    actual_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    actual_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    target_episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_summary: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_collection_tasks_project_code"),
        UniqueConstraint("project_id", "id", name="uq_collection_tasks_project_id_id"),
        ForeignKeyConstraint(
            ["project_id", "assigned_robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_collection_tasks_project_assigned_robot",
            ondelete="RESTRICT",
        ),
        _enum_check("status", _TASK_STATUS_VALUES, "status_allowed"),
        _enum_check("priority", _PRIORITY_VALUES, "priority_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("target_episode_count >= 0", name="target_episode_count_non_negative"),
        CheckConstraint("target_duration_sec >= 0", name="target_duration_sec_non_negative"),
        CheckConstraint("submitted_episode_count >= 0", name="submitted_count_non_negative"),
        CheckConstraint("accepted_episode_count >= 0", name="accepted_count_non_negative"),
        CheckConstraint(
            "scheduled_end IS NULL OR scheduled_start IS NULL "
            "OR scheduled_end >= scheduled_start",
            name="scheduled_range_valid",
        ),
        CheckConstraint(
            "actual_end IS NULL OR actual_start IS NULL OR actual_end >= actual_start",
            name="actual_range_valid",
        ),
        Index(
            "ix_collection_tasks_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_collection_tasks_project_assigned_to_status",
            "project_id",
            "assigned_to",
            "status",
        ),
    )


class CollectionBatch(Base, ProjectScopedAuditMixin):
    __tablename__ = "collection_batches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_to: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    scheduled_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_collection_batches_project_id_id"),
        UniqueConstraint("project_id", "code", name="uq_collection_batches_project_code"),
        UniqueConstraint(
            "project_id",
            "task_id",
            "sequence_no",
            name="uq_collection_batches_project_task_sequence",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_batches_project_task",
            ondelete="RESTRICT",
        ),
        _enum_check("status", _BATCH_STATUS_VALUES, "status_allowed"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("expected_count >= 0", name="expected_count_non_negative"),
        CheckConstraint("submitted_count >= 0", name="submitted_count_non_negative"),
        CheckConstraint("accepted_count >= 0", name="accepted_count_non_negative"),
        CheckConstraint(
            "scheduled_end IS NULL OR scheduled_start IS NULL "
            "OR scheduled_end >= scheduled_start",
            name="scheduled_range_valid",
        ),
        Index("ix_collection_batches_project_task_status", "project_id", "task_id", "status"),
        Index("ix_collection_batches_project_sequence_no", "project_id", "sequence_no"),
    )


class CollectionTaskDevice(Base):
    __tablename__ = "collection_task_devices"

    project_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    assigned_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_task_devices_project_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "device_id"],
            ["devices.project_id", "devices.id"],
            name="fk_collection_task_devices_project_device",
            ondelete="RESTRICT",
        ),
        Index("ix_collection_task_devices_project_device", "project_id", "device_id"),
    )


class CollectionTaskSensor(Base):
    __tablename__ = "collection_task_sensors"

    project_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sensor_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    assigned_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_task_sensors_project_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "sensor_id"],
            ["sensors.project_id", "sensors.id"],
            name="fk_collection_task_sensors_project_sensor",
            ondelete="RESTRICT",
        ),
        Index("ix_collection_task_sensors_project_sensor", "project_id", "sensor_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id"),
        nullable=False,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint("result IN ('SUCCESS', 'FAILURE')", name="result_allowed"),
        Index(
            "ix_audit_logs_project_resource",
            "project_id",
            "resource_type",
            "resource_id",
            "occurred_at",
        ),
        Index("ix_audit_logs_project_occurred_at", "project_id", "occurred_at"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id"),
        nullable=False,
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    __table_args__ = (
        _enum_check("status", _OUTBOX_STATUS_VALUES, "status_allowed"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_outbox_events_status_available_at", "status", "available_at"),
        Index(
            "ix_outbox_events_project_aggregate",
            "project_id",
            "aggregate_type",
            "aggregate_id",
        ),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("response_status BETWEEN 100 AND 599", name="response_status_valid"),
        Index("ix_idempotency_records_expires_at", "expires_at"),
    )
