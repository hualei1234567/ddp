"""Edge Agent ORM 行到领域对象的映射辅助。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.domain.edge_agents import (
    AgentStatus,
    EdgeAgent,
    HeartbeatReport,
    Lease,
    LeaseReleaseReason,
    PairingToken,
)
from app.infrastructure.db.models.edge_agents import (
    AgentHeartbeatModel,
    AgentTaskLeaseModel,
    EdgeAgentModel,
    EdgeAgentPairingTokenModel,
)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_utc_required(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_uuids(raw: list[str] | None) -> tuple[UUID, ...]:
    return tuple(UUID(item) for item in (raw or ()))


def pairing_from_row(row: EdgeAgentPairingTokenModel) -> PairingToken:
    return PairingToken(
        token_id=row.id,
        project_id=row.project_id,
        robot_id=row.robot_id,
        display_name=row.display_name,
        created_by=row.created_by,
        created_at=as_utc_required(row.created_at),
        expires_at=as_utc_required(row.expires_at),
        device_ids=parse_uuids(row.device_ids),
        consumed_at=as_utc(row.consumed_at),
        agent_id=row.agent_id,
    )


def agent_from_row(row: EdgeAgentModel) -> EdgeAgent:
    return EdgeAgent(
        agent_id=row.id,
        project_id=row.project_id,
        display_name=row.display_name,
        robot_id=row.robot_id,
        status=AgentStatus(row.status),
        created_by=row.created_by,
        created_at=as_utc_required(row.created_at),
        paired_at=as_utc_required(row.paired_at),
        device_ids=parse_uuids(row.device_ids),
        dispatch_paused=row.dispatch_paused,
        activated_at=as_utc(row.activated_at),
        last_seen_at=as_utc(row.last_seen_at),
        agent_version=row.agent_version,
        os_arch=row.os_arch,
        token_generation=row.token_generation,
        token_rotated_at=as_utc(row.token_rotated_at),
        revoked_at=as_utc(row.revoked_at),
        revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason,
    )


def heartbeat_from_row(row: AgentHeartbeatModel) -> HeartbeatReport:
    return HeartbeatReport(
        reported_at=as_utc_required(row.reported_at),
        agent_version=row.agent_version,
        os=row.os,
        arch=row.arch,
        network_reachable=row.network_reachable,
        disk_free_bytes=row.disk_free_bytes,
        spool_pending_count=row.spool_pending_count,
        spool_pending_bytes=row.spool_pending_bytes,
        upload_queue_depth=row.upload_queue_depth,
        adapter_versions=dict(row.adapter_versions or {}),
        current_session_id=row.current_session_id,
        last_error=row.last_error,
    )


def lease_from_row(row: AgentTaskLeaseModel) -> Lease:
    return Lease(
        lease_id=row.id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        session_id=row.capture_session_id,
        device_ids=parse_uuids(row.device_ids),
        acquired_at=as_utc_required(row.acquired_at),
        expires_at=as_utc_required(row.expires_at),
        last_renewed_at=as_utc_required(row.last_renewed_at),
        released_at=as_utc(row.released_at),
        release_reason=(
            LeaseReleaseReason(row.release_reason) if row.release_reason is not None else None
        ),
        release_note=row.release_note,
    )
