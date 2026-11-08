"""stage3 annotation tasks, batches, assignments and workload events

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09

阶段 3 第一批标注域（下）：任务、批次、分配与分配事件。
- 任务绑定已快照样本集与已发布 Schema；绑定字段不可变；
- 批次不得领先任务，分配不得领先任务/批次；
- 分配事件只追加，作为工作量周期汇总的真实来源。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.annotation_work_guards import (
    drop_work_guards,
    install_work_guards,
)

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


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
        "annotation_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sample_set_id", sa.Uuid(), nullable=False),
        sa.Column("schema_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("schema_sha256", sa.String(length=71), nullable=False),
        sa.Column("gold_set_id", sa.Uuid(), nullable=True),
        sa.Column("iaa_policy", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("quality_policy", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("assignment_mode", sa.String(length=16), nullable=False),
        sa.Column("target_item_count", sa.Integer(), nullable=False),
        sa.Column("target_episode_count", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("progress", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("quality_summary", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("iaa_report_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        sa.Column("blocked_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_tasks"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_tasks_project_id_id"),
        sa.UniqueConstraint("project_id", "code", name="uq_annotation_tasks_project_code"),
        sa.ForeignKeyConstraint(
            ["project_id", "sample_set_id"],
            ["annotation_sample_sets.project_id", "annotation_sample_sets.id"],
            name="fk_annotation_tasks_project_sample_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "schema_id"],
            ["annotation_schemas.project_id", "annotation_schemas.id"],
            name="fk_annotation_tasks_project_schema",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'READY', 'ASSIGNED', "
            "'IN_PROGRESS', 'PAUSED', 'BLOCKED', 'SUBMITTED', 'REVIEWING', "
            "'ARBITRATION', 'ACCEPTED', 'REJECTED', 'REWORK', 'COMPLETED', "
            "'CANCELLED', 'ARCHIVED')",
            name=op.f("ck_annotation_tasks_status_allowed"),
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')",
            name=op.f("ck_annotation_tasks_priority_allowed"),
        ),
        sa.CheckConstraint(
            "assignment_mode IN ('MANUAL', 'CLAIM', 'MIXED')",
            name=op.f("ck_annotation_tasks_assignment_mode_allowed"),
        ),
        sa.CheckConstraint(
            "length(schema_sha256) = 71 AND substr(schema_sha256, 1, 7) = 'sha256:'",
            name=op.f("ck_annotation_tasks_schema_sha256_format"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_annotation_tasks_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "target_item_count >= 1",
            name=op.f("ck_annotation_tasks_target_item_count_positive"),
        ),
        sa.CheckConstraint(
            "target_episode_count >= 1",
            name=op.f("ck_annotation_tasks_target_episode_count_positive"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_annotation_tasks_version_positive")),
        *_audit_constraints("annotation_tasks"),
    )
    op.create_index(
        "ix_annotation_tasks_project_status_created_at",
        "annotation_tasks",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_tasks_project_sample_set",
        "annotation_tasks",
        ["project_id", "sample_set_id"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_tasks_project_schema",
        "annotation_tasks",
        ["project_id", "schema_id"],
        unique=False,
    )

    op.create_table(
        "annotation_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stratum", sa.String(length=100), nullable=True),
        sa.Column("item_refs", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("planned_item_count", sa.Integer(), nullable=False),
        sa.Column("assignee_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "reviewer_pool_ids",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_item_count", sa.Integer(), nullable=False),
        sa.Column("approved_item_count", sa.Integer(), nullable=False),
        sa.Column("rejected_item_count", sa.Integer(), nullable=False),
        sa.Column("rework_count", sa.Integer(), nullable=False),
        sa.Column("quality_summary", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_batches"),
        sa.UniqueConstraint("project_id", "id", name="uq_annotation_batches_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "id",
            name="uq_annotation_batches_project_task_id",
        ),
        sa.UniqueConstraint("project_id", "code", name="uq_annotation_batches_project_code"),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "sequence_no",
            name="uq_annotation_batches_project_task_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_batches_project_task",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'READY', 'ASSIGNED', 'IN_PROGRESS', 'PAUSED', "
            "'BLOCKED', 'SUBMITTED', 'REVIEWING', 'ARBITRATION', 'ACCEPTED', "
            "'REJECTED', 'REWORK', 'CANCELLED')",
            name=op.f("ck_annotation_batches_status_allowed"),
        ),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name=op.f("ck_annotation_batches_sequence_positive"),
        ),
        sa.CheckConstraint(
            "planned_item_count >= 0",
            name=op.f("ck_annotation_batches_planned_item_count_non_negative"),
        ),
        sa.CheckConstraint(
            "submitted_item_count >= 0",
            name=op.f("ck_annotation_batches_submitted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "approved_item_count >= 0",
            name=op.f("ck_annotation_batches_approved_count_non_negative"),
        ),
        sa.CheckConstraint(
            "rejected_item_count >= 0",
            name=op.f("ck_annotation_batches_rejected_count_non_negative"),
        ),
        sa.CheckConstraint(
            "rework_count >= 0",
            name=op.f("ck_annotation_batches_rework_count_non_negative"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_annotation_batches_version_positive")),
        *_audit_constraints("annotation_batches"),
    )
    op.create_index(
        "ix_annotation_batches_project_task_status",
        "annotation_batches",
        ["project_id", "task_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_batches_project_sequence_no",
        "annotation_batches",
        ["project_id", "sequence_no"],
        unique=False,
    )

    op.create_table(
        "annotation_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("planned_item_count", sa.Integer(), nullable=False),
        sa.Column("claimed_item_count", sa.Integer(), nullable=False),
        sa.Column("submitted_item_count", sa.Integer(), nullable=False),
        sa.Column("approved_item_count", sa.Integer(), nullable=False),
        sa.Column("planned_minutes", sa.Integer(), nullable=False),
        sa.Column("actual_minutes", sa.Integer(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=1000), nullable=True),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_assignments"),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_assignments_project_id_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "batch_id",
            "assignee_id",
            "assignment_role",
            name="uq_annotation_assignments_batch_assignee_role",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["annotation_tasks.project_id", "annotation_tasks.id"],
            name="fk_annotation_assignments_project_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id", "batch_id"],
            [
                "annotation_batches.project_id",
                "annotation_batches.task_id",
                "annotation_batches.id",
            ],
            name="fk_annotation_assignments_project_task_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "assignee_id"],
            ["project_members.project_id", "project_members.user_id"],
            name="fk_annotation_assignments_project_assignee",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "assignment_role IN ('ANNOTATOR', 'REVIEWER', 'ARBITER')",
            name=op.f("ck_annotation_assignments_assignment_role_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('ASSIGNED', 'CLAIMED', 'IN_PROGRESS', 'SUBMITTED', "
            "'COMPLETED', 'REVOKED', 'EXPIRED')",
            name=op.f("ck_annotation_assignments_status_allowed"),
        ),
        sa.CheckConstraint(
            "planned_item_count >= 0",
            name=op.f("ck_annotation_assignments_planned_item_count_non_negative"),
        ),
        sa.CheckConstraint(
            "claimed_item_count >= 0",
            name=op.f("ck_annotation_assignments_claimed_count_non_negative"),
        ),
        sa.CheckConstraint(
            "submitted_item_count >= 0",
            name=op.f("ck_annotation_assignments_submitted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "approved_item_count >= 0",
            name=op.f("ck_annotation_assignments_approved_count_non_negative"),
        ),
        sa.CheckConstraint(
            "planned_minutes >= 0",
            name=op.f("ck_annotation_assignments_planned_minutes_non_negative"),
        ),
        sa.CheckConstraint(
            "actual_minutes IS NULL OR actual_minutes >= 0",
            name=op.f("ck_annotation_assignments_actual_minutes_non_negative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_annotation_assignments_version_positive"),
        ),
        *_audit_constraints("annotation_assignments"),
    )
    op.create_index(
        "ix_annotation_assignments_project_assignee_status",
        "annotation_assignments",
        ["project_id", "assignee_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_assignments_project_batch_status",
        "annotation_assignments",
        ["project_id", "batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_assignments_project_task_status",
        "annotation_assignments",
        ["project_id", "task_id", "status"],
        unique=False,
    )

    op.create_table(
        "annotation_assignment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("planned_item_count", sa.Integer(), nullable=False),
        sa.Column("submitted_item_count", sa.Integer(), nullable=False),
        sa.Column("approved_item_count", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_assignment_events"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_annotation_assignment_events_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "assignment_id"],
            ["annotation_assignments.project_id", "annotation_assignments.id"],
            name="fk_annotation_assignment_events_project_assignment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_annotation_assignment_events_actor_id_users",
        ),
        sa.CheckConstraint(
            "length(action) >= 1",
            name=op.f("ck_annotation_assignment_events_action_non_empty"),
        ),
        sa.CheckConstraint(
            "planned_item_count >= 0",
            name=op.f("ck_annotation_assignment_events_planned_count_non_negative"),
        ),
        sa.CheckConstraint(
            "submitted_item_count >= 0",
            name=op.f("ck_annotation_assignment_events_submitted_count_non_negative"),
        ),
        sa.CheckConstraint(
            "approved_item_count >= 0",
            name=op.f("ck_annotation_assignment_events_approved_count_non_negative"),
        ),
        sa.CheckConstraint(
            "minutes >= 0",
            name=op.f("ck_annotation_assignment_events_minutes_non_negative"),
        ),
    )
    op.create_index(
        "ix_annotation_assignment_events_project_assignee_time",
        "annotation_assignment_events",
        ["project_id", "assignee_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_annotation_assignment_events_project_assignment",
        "annotation_assignment_events",
        ["project_id", "assignment_id"],
        unique=False,
    )

    install_work_guards(op.get_bind())


def downgrade() -> None:
    drop_work_guards(op.get_bind())
    op.drop_index(
        "ix_annotation_assignment_events_project_assignment",
        table_name="annotation_assignment_events",
    )
    op.drop_index(
        "ix_annotation_assignment_events_project_assignee_time",
        table_name="annotation_assignment_events",
    )
    op.drop_table("annotation_assignment_events")
    op.drop_index(
        "ix_annotation_assignments_project_task_status",
        table_name="annotation_assignments",
    )
    op.drop_index(
        "ix_annotation_assignments_project_batch_status",
        table_name="annotation_assignments",
    )
    op.drop_index(
        "ix_annotation_assignments_project_assignee_status",
        table_name="annotation_assignments",
    )
    op.drop_table("annotation_assignments")
    op.drop_index(
        "ix_annotation_batches_project_sequence_no",
        table_name="annotation_batches",
    )
    op.drop_index(
        "ix_annotation_batches_project_task_status",
        table_name="annotation_batches",
    )
    op.drop_table("annotation_batches")
    op.drop_index(
        "ix_annotation_tasks_project_schema",
        table_name="annotation_tasks",
    )
    op.drop_index(
        "ix_annotation_tasks_project_sample_set",
        table_name="annotation_tasks",
    )
    op.drop_index(
        "ix_annotation_tasks_project_status_created_at",
        table_name="annotation_tasks",
    )
    op.drop_table("annotation_tasks")
