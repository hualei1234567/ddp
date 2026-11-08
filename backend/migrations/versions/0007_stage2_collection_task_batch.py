"""stage2 collection tasks, batches and assignments

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08

建立采集任务、批次与任务设备/传感器分配表：
- 所有表强制 ``project_id``，父引用使用 ``(project_id, id)`` 复合外键；
- 状态由 CHECK + 触发器双重校验，终态不可回退、记录不可删除；
- 批次触发器额外校验“批次不得领先任务”。

阶段 2 尚无历史采集数据，本迁移不做回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_collection_state_guards,
    install_collection_state_guards,
)

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TASK_STATUS_VALUES = (
    "'DRAFT', 'PENDING_REVIEW', 'APPROVED', 'READY', 'ASSIGNED', "
    "'IN_PROGRESS', 'PAUSED', 'BLOCKED', 'SUBMITTED', 'DATA_CHECKING', "
    "'ACCEPTED', 'REJECTED', 'REWORK', 'COMPLETED', 'CANCELLED'"
)
_BATCH_STATUS_VALUES = (
    "'PLANNED', 'READY', 'IN_PROGRESS', 'PAUSED', 'BLOCKED', "
    "'SUBMITTED', 'ACCEPTED', 'REJECTED', 'REWORK', 'CANCELLED'"
)


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def _audit_constraints(table: str) -> list[sa.schema.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{table}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{table}_updated_by_users",
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "collection_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=True),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("sop_id", sa.Uuid(), nullable=True),
        sa.Column("sop_version", sa.String(length=64), nullable=True),
        sa.Column("scenario_id", sa.Uuid(), nullable=True),
        sa.Column("action_spec_id", sa.Uuid(), nullable=True),
        sa.Column("action_spec_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("assigned_robot_id", sa.Uuid(), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "target_episode_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "target_duration_sec",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "submitted_episode_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "accepted_episode_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("quality_summary", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        sa.Column("blocked_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_collection_tasks"),
        sa.UniqueConstraint("project_id", "code", name="uq_collection_tasks_project_code"),
        sa.UniqueConstraint("project_id", "id", name="uq_collection_tasks_project_id_id"),
        sa.ForeignKeyConstraint(
            ["project_id", "assigned_robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_collection_tasks_project_assigned_robot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name="fk_collection_tasks_assigned_to_users",
        ),
        sa.CheckConstraint(
            f"status IN ({_TASK_STATUS_VALUES})",
            name=op.f("ck_collection_tasks_status_allowed"),
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2')",
            name=op.f("ck_collection_tasks_priority_allowed"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_collection_tasks_version_positive")),
        sa.CheckConstraint(
            "target_episode_count >= 0",
            name=op.f("ck_collection_tasks_target_episode_count_non_negative"),
        ),
        sa.CheckConstraint(
            "target_duration_sec >= 0",
            name=op.f("ck_collection_tasks_target_duration_sec_non_negative"),
        ),
        sa.CheckConstraint(
            "submitted_episode_count >= 0",
            name=op.f("ck_collection_tasks_submitted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "accepted_episode_count >= 0",
            name=op.f("ck_collection_tasks_accepted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "scheduled_end IS NULL OR scheduled_start IS NULL "
            "OR scheduled_end >= scheduled_start",
            name=op.f("ck_collection_tasks_scheduled_range_valid"),
        ),
        sa.CheckConstraint(
            "actual_end IS NULL OR actual_start IS NULL OR actual_end >= actual_start",
            name=op.f("ck_collection_tasks_actual_range_valid"),
        ),
        *_audit_constraints("collection_tasks"),
    )
    op.create_index(
        "ix_collection_tasks_project_status_created_at",
        "collection_tasks",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_collection_tasks_project_assigned_to_status",
        "collection_tasks",
        ["project_id", "assigned_to", "status"],
        unique=False,
    )

    op.create_table(
        "collection_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_to", sa.Uuid(), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("submitted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("accepted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_collection_batches"),
        sa.UniqueConstraint("project_id", "id", name="uq_collection_batches_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_collection_batches_project_code"),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "sequence_no",
            name="uq_collection_batches_project_task_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_batches_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            name="fk_collection_batches_assigned_to_users",
        ),
        sa.CheckConstraint(
            f"status IN ({_BATCH_STATUS_VALUES})",
            name=op.f("ck_collection_batches_status_allowed"),
        ),
        sa.CheckConstraint(
            "sequence_no >= 1", name=op.f("ck_collection_batches_sequence_positive")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_collection_batches_version_positive")),
        sa.CheckConstraint(
            "expected_count >= 0",
            name=op.f("ck_collection_batches_expected_count_non_negative"),
        ),
        sa.CheckConstraint(
            "submitted_count >= 0",
            name=op.f("ck_collection_batches_submitted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "accepted_count >= 0",
            name=op.f("ck_collection_batches_accepted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "scheduled_end IS NULL OR scheduled_start IS NULL "
            "OR scheduled_end >= scheduled_start",
            name=op.f("ck_collection_batches_scheduled_range_valid"),
        ),
        *_audit_constraints("collection_batches"),
    )
    op.create_index(
        "ix_collection_batches_project_task_status",
        "collection_batches",
        ["project_id", "task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_collection_batches_project_sequence_no",
        "collection_batches",
        ["project_id", "sequence_no"],
        unique=False,
    )

    op.create_table(
        "collection_task_devices",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "task_id",
            "device_id",
            name="pk_collection_task_devices",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_task_devices_project_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "device_id"],
            ["devices.project_id", "devices.id"],
            name="fk_collection_task_devices_project_device",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            name="fk_collection_task_devices_assigned_by_users",
        ),
    )
    op.create_index(
        "ix_collection_task_devices_project_device",
        "collection_task_devices",
        ["project_id", "device_id"],
        unique=False,
    )

    op.create_table(
        "collection_task_sensors",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("sensor_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "task_id",
            "sensor_id",
            name="pk_collection_task_sensors",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["collection_tasks.project_id", "collection_tasks.id"],
            name="fk_collection_task_sensors_project_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "sensor_id"],
            ["sensors.project_id", "sensors.id"],
            name="fk_collection_task_sensors_project_sensor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            name="fk_collection_task_sensors_assigned_by_users",
        ),
    )
    op.create_index(
        "ix_collection_task_sensors_project_sensor",
        "collection_task_sensors",
        ["project_id", "sensor_id"],
        unique=False,
    )

    install_collection_state_guards(op.get_bind())


def downgrade() -> None:
    drop_collection_state_guards(op.get_bind())
    op.drop_index(
        "ix_collection_task_sensors_project_sensor",
        table_name="collection_task_sensors",
    )
    op.drop_table("collection_task_sensors")
    op.drop_index(
        "ix_collection_task_devices_project_device",
        table_name="collection_task_devices",
    )
    op.drop_table("collection_task_devices")
    op.drop_index(
        "ix_collection_batches_project_sequence_no",
        table_name="collection_batches",
    )
    op.drop_index(
        "ix_collection_batches_project_task_status",
        table_name="collection_batches",
    )
    op.drop_table("collection_batches")
    op.drop_index(
        "ix_collection_tasks_project_assigned_to_status",
        table_name="collection_tasks",
    )
    op.drop_index(
        "ix_collection_tasks_project_status_created_at",
        table_name="collection_tasks",
    )
    op.drop_table("collection_tasks")
