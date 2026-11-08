"""stage2 audit, outbox and idempotency

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08

阶段 2 前置基础设施：追加式审计、事务性 outbox 与幂等记录。
当前分支 0001–0005 尚无同名表，因此本迁移直接创建；若未来其他分支已落地
同名结构，应改为幂等检测/合并，禁止重复建表。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.collection_guards import (
    drop_audit_guards,
    install_audit_guards,
)

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result IN ('SUCCESS', 'FAILURE')",
            name=op.f("ck_audit_logs_result_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_audit_logs_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index(
        "ix_audit_logs_project_resource",
        "audit_logs",
        ["project_id", "resource_type", "resource_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_project_occurred_at",
        "audit_logs",
        ["project_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PUBLISHED', 'FAILED')",
            name=op.f("ck_outbox_events_status_allowed"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_events_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_outbox_events_project_id_projects",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index(
        "ix_outbox_events_status_available_at",
        "outbox_events",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_project_aggregate",
        "outbox_events",
        ["project_id", "aggregate_type", "aggregate_id"],
        unique=False,
    )

    op.create_table(
        "idempotency_records",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("request_hash", sa.String(length=71), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_ref", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name=op.f("ck_idempotency_records_response_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_idempotency_records_project_id_projects",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_idempotency_records_user_id_users",
        ),
        sa.PrimaryKeyConstraint(
            "project_id",
            "user_id",
            "key",
            name="pk_idempotency_records",
        ),
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
        unique=False,
    )

    install_audit_guards(op.get_bind())


def downgrade() -> None:
    drop_audit_guards(op.get_bind())
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_outbox_events_project_aggregate", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status_available_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_audit_logs_project_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_project_resource", table_name="audit_logs")
    op.drop_table("audit_logs")
