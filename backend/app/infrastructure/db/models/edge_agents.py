"""Edge Agent ORM 模型（V31-011 / ADR-0047）。

四张表：``edge_agents`` / ``edge_agent_pairing_tokens`` / ``agent_heartbeats`` /
``agent_task_leases``。设计要点：

- 全部使用 ``(project_id, id)`` 复合外键，Agent / Lease / 心跳**永不跨项目**引用
  机器人、采集会话或 Agent（与 ``capture_sessions`` 同口径）；
- 凭据只存 SHA-256 摘要（``token_hash``），没有明文列，也没有可逆列；
- 同一 CaptureSession 同时只允许一个**未释放** Lease：部分唯一索引
  ``uq_agent_task_leases_active_session``（PostgreSQL 与 SQLite 都支持
  ``WHERE`` 的部分索引，迁移与 ORM 使用同一份谓词）；
- 这四张表的写入者包含 Agent 自身，因此不套用 ``ProjectScopedAuditMixin`` 的
  ``updated_by``（FK 到 ``users``）；人类操作者记录在 ``created_by`` /
  ``revoked_by``，Agent 自己的行为靠 ``last_seen_at`` 与审计日志追溯。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

from app.domain.edge_agents.enums import AgentStatus, LeaseReleaseReason
from app.infrastructure.db.base import Base

_AGENT_STATUS_VALUES = ", ".join(repr(item.value) for item in AgentStatus)
_RELEASE_REASON_VALUES = ", ".join(repr(item.value) for item in LeaseReleaseReason)
#: 部分索引谓词：迁移 0071 使用同一字面量。
ACTIVE_LEASE_PREDICATE = "released_at IS NULL"


def _now_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class EdgeAgentModel(Base):
    __tablename__ = "edge_agents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    robot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    device_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    dispatch_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_arch: Mapped[str | None] = mapped_column(String(96), nullable=True)
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_edge_agents_project_id_id"),
        UniqueConstraint("token_hash", name="uq_edge_agents_token_hash"),
        ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_edge_agents_project_robot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"status IN ({_AGENT_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("token_generation >= 1", name="token_generation_positive"),
        CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL)",
            name="revoked_fields_consistent",
        ),
        Index("ix_edge_agents_project_status_created_at", "project_id", "status", "created_at"),
        Index("ix_edge_agents_project_robot", "project_id", "robot_id"),
    )


class EdgeAgentPairingTokenModel(Base):
    __tablename__ = "edge_agent_pairing_tokens"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    robot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    device_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_edge_agent_pairing_tokens_token_hash"),
        ForeignKeyConstraint(
            ["project_id", "robot_id"],
            ["robots.project_id", "robots.id"],
            name="fk_edge_agent_pairing_tokens_project_robot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_edge_agent_pairing_tokens_project_agent",
            ondelete="RESTRICT",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(consumed_at IS NULL) = (agent_id IS NULL)",
            name="consumed_fields_consistent",
        ),
        Index("ix_edge_agent_pairing_tokens_project_created_at", "project_id", "created_at"),
    )


class AgentHeartbeatModel(Base):
    __tablename__ = "agent_heartbeats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    os: Mapped[str] = mapped_column(String(64), nullable=False)
    arch: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_versions: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    disk_free_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    network_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    current_session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    spool_pending_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spool_pending_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_queue_depth: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_agent_heartbeats_project_agent",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "current_session_id"],
            ["capture_sessions.project_id", "capture_sessions.id"],
            name="fk_agent_heartbeats_project_session",
            ondelete="NO ACTION",
        ),
        # 同一 Agent 以自报时间戳去重：网络重试同一条心跳是 upsert，不是新增。
        UniqueConstraint("agent_id", "reported_at", name="uq_agent_heartbeats_agent_reported"),
        CheckConstraint(
            "disk_free_bytes >= 0 AND spool_pending_count >= 0 "
            "AND spool_pending_bytes >= 0 AND upload_queue_depth >= 0",
            name="counters_non_negative",
        ),
        Index("ix_agent_heartbeats_agent_received_at", "agent_id", "received_at"),
    )


class AgentTaskLeaseModel(Base):
    __tablename__ = "agent_task_leases"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    capture_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    device_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["edge_agents.project_id", "edge_agents.id"],
            name="fk_agent_task_leases_project_agent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "capture_session_id"],
            ["capture_sessions.project_id", "capture_sessions.id"],
            name="fk_agent_task_leases_project_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"release_reason IS NULL OR release_reason IN ({_RELEASE_REASON_VALUES})",
            name="release_reason_allowed",
        ),
        CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)",
            name="release_fields_consistent",
        ),
        CheckConstraint("expires_at > acquired_at", name="expiry_after_acquisition"),
        Index(
            "uq_agent_task_leases_active_session",
            "capture_session_id",
            unique=True,
            postgresql_where=text(ACTIVE_LEASE_PREDICATE),
            sqlite_where=text(ACTIVE_LEASE_PREDICATE),
        ),
        Index("ix_agent_task_leases_agent_released_at", "agent_id", "released_at"),
        Index("ix_agent_task_leases_project_session", "project_id", "capture_session_id"),
    )


__all__ = [
    "ACTIVE_LEASE_PREDICATE",
    "AgentHeartbeatModel",
    "AgentTaskLeaseModel",
    "EdgeAgentModel",
    "EdgeAgentPairingTokenModel",
]
