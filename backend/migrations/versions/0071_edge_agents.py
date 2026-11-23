"""V31-011：Edge Capture Agent 身份、配对凭证、心跳与任务 Lease 表。

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-23

设计依据：ADR-0047（纯拉取模式）与 V4.1 蓝图 04（FR-001/002/003）。
四张表全部使用 ``(project_id, id)`` 复合外键保证跨项目隔离；凭据仅存 SHA-256
摘要，不存明文；``agent_task_leases`` 通过部分唯一索引
``uq_agent_task_leases_active_session``（``WHERE released_at IS NULL``）在数据库层
保证同一 CaptureSession 同时只有一个未释放的 active Lease。

回滚：``downgrade()`` 按依赖逆序删除四张表与索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

_AGENT_STATUSES = "'PAIRED', 'ACTIVE', 'REVOKED'"
_RELEASE_REASONS = (
    "'COMPLETED', 'RELEASED_BY_AGENT', 'FAILED', " "'EXPIRED', 'AGENT_REVOKED', 'ADMIN_RECLAIMED'"
)
_ACTIVE_LEASE_PREDICATE = "released_at IS NULL"


def _create_edge_agents() -> None:
    op.create_table(
        "edge_agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("robot_id", sa.Uuid(), nullable=False),
        sa.Column("device_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("dispatch_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("os_arch", sa.String(length=96), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_edge_agents"),
        sa.UniqueConstraint("project_id", "id", name="uq_edge_agents_project_id_id"),
        sa.UniqueConstraint("token_hash", name="uq_edge_agents_token_hash"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_edge_agents_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_edge_agents_project_robot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_edge_agents_created_by"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], name="fk_edge_agents_revoked_by"),
        sa.CheckConstraint(
            f"status IN ({_AGENT_STATUSES})",
            name=op.f("ck_edge_agents_status_allowed"),
        ),
        sa.CheckConstraint(
            "token_generation >= 1",
            name=op.f("ck_edge_agents_token_generation_positive"),
        ),
        sa.CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL)",
            name=op.f("ck_edge_agents_revoked_fields_consistent"),
        ),
    )
    op.create_index(
        "ix_edge_agents_project_status_created_at",
        "edge_agents",
        ["project_id", "status", "created_at"],
    )
    op.create_index("ix_edge_agents_project_robot", "edge_agents", ["project_id", "robot_id"])


def _create_pairing_tokens() -> None:
    op.create_table(
        "edge_agent_pairing_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("robot_id", sa.Uuid(), nullable=False),
        sa.Column("device_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_edge_agent_pairing_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_edge_agent_pairing_tokens_token_hash"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_edge_agent_pairing_tokens_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_edge_agent_pairing_tokens_project_robot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_edge_agent_pairing_tokens_project_agent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_edge_agent_pairing_tokens_created_by",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_edge_agent_pairing_tokens_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (agent_id IS NULL)",
            name=op.f("ck_edge_agent_pairing_tokens_consumed_fields_consistent"),
        ),
    )
    op.create_index(
        "ix_edge_agent_pairing_tokens_project_created_at",
        "edge_agent_pairing_tokens",
        ["project_id", "created_at"],
    )


def _create_heartbeats() -> None:
    op.create_table(
        "agent_heartbeats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_version", sa.String(length=64), nullable=False),
        sa.Column("os", sa.String(length=64), nullable=False),
        sa.Column("arch", sa.String(length=32), nullable=False),
        sa.Column("adapter_versions", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("disk_free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("network_reachable", sa.Boolean(), nullable=False),
        sa.Column("current_session_id", sa.Uuid(), nullable=True),
        sa.Column("spool_pending_count", sa.BigInteger(), nullable=False),
        sa.Column("spool_pending_bytes", sa.BigInteger(), nullable=False),
        sa.Column("upload_queue_depth", sa.BigInteger(), nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_heartbeats"),
        sa.UniqueConstraint("agent_id", "reported_at", name="uq_agent_heartbeats_agent_reported"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_heartbeats_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_agent_heartbeats_project_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "current_session_id"],
            ["capture_sessions.project_id", "capture_sessions.id"],
            name="fk_agent_heartbeats_project_session",
            ondelete="NO ACTION",
        ),
        sa.CheckConstraint(
            "disk_free_bytes >= 0 AND spool_pending_count >= 0 "
            "AND spool_pending_bytes >= 0 AND upload_queue_depth >= 0",
            name=op.f("ck_agent_heartbeats_counters_non_negative"),
        ),
    )
    op.create_index(
        "ix_agent_heartbeats_agent_received_at",
        "agent_heartbeats",
        ["agent_id", "received_at"],
    )


def _create_leases() -> None:
    op.create_table(
        "agent_task_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("capture_session_id", sa.Uuid(), nullable=False),
        sa.Column("device_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=32), nullable=True),
        sa.Column("release_note", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_task_leases"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_task_leases_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_agent_task_leases_project_agent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "capture_session_id"],
            ["capture_sessions.project_id", "capture_sessions.id"],
            name="fk_agent_task_leases_project_session",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"release_reason IS NULL OR release_reason IN ({_RELEASE_REASONS})",
            name=op.f("ck_agent_task_leases_release_reason_allowed"),
        ),
        sa.CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)",
            name=op.f("ck_agent_task_leases_release_fields_consistent"),
        ),
        sa.CheckConstraint(
            "expires_at > acquired_at",
            name=op.f("ck_agent_task_leases_expiry_after_acquisition"),
        ),
    )
    op.create_index(
        "uq_agent_task_leases_active_session",
        "agent_task_leases",
        ["capture_session_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_LEASE_PREDICATE),
        sqlite_where=sa.text(_ACTIVE_LEASE_PREDICATE),
    )
    op.create_index(
        "ix_agent_task_leases_agent_released_at",
        "agent_task_leases",
        ["agent_id", "released_at"],
    )
    op.create_index(
        "ix_agent_task_leases_project_session",
        "agent_task_leases",
        ["project_id", "capture_session_id"],
    )


def upgrade() -> None:
    _create_edge_agents()
    _create_pairing_tokens()
    _create_heartbeats()
    _create_leases()


def downgrade() -> None:
    op.drop_index("ix_agent_task_leases_project_session", table_name="agent_task_leases")
    op.drop_index("ix_agent_task_leases_agent_released_at", table_name="agent_task_leases")
    op.drop_index("uq_agent_task_leases_active_session", table_name="agent_task_leases")
    op.drop_table("agent_task_leases")

    op.drop_index("ix_agent_heartbeats_agent_received_at", table_name="agent_heartbeats")
    op.drop_table("agent_heartbeats")

    op.drop_index(
        "ix_edge_agent_pairing_tokens_project_created_at",
        table_name="edge_agent_pairing_tokens",
    )
    op.drop_table("edge_agent_pairing_tokens")

    op.drop_index("ix_edge_agents_project_robot", table_name="edge_agents")
    op.drop_index("ix_edge_agents_project_status_created_at", table_name="edge_agents")
    op.drop_table("edge_agents")
