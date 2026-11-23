"""Edge Agent API DTO（请求约束与响应序列化）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.edge_agents import (
    AgentDetailView,
    AgentView,
    AssignmentItem,
    AssignmentListResult,
    HeartbeatResult,
    IssuedPairingToken,
    PairedAgentResult,
    RotatedAgentToken,
)
from app.domain.edge_agents import HeartbeatReport, Lease


class PairingTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    robot_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    device_ids: list[UUID] = Field(default_factory=list, max_length=64)
    ttl_seconds: int | None = Field(default=None, ge=1, le=86400)


class PairingTokenIssuedOut(BaseModel):
    token_id: UUID
    project_id: UUID
    robot_id: UUID
    display_name: str
    device_ids: list[UUID]
    pairing_token: str
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_result(cls, result: IssuedPairingToken) -> PairingTokenIssuedOut:
        return cls(
            token_id=result.token.token_id,
            project_id=result.token.project_id,
            robot_id=result.token.robot_id,
            display_name=result.token.display_name,
            device_ids=list(result.token.device_ids),
            pairing_token=result.pairing_token,
            created_at=result.token.created_at,
            expires_at=result.token.expires_at,
        )


class AgentHeartbeatSummaryOut(BaseModel):
    reported_at: datetime
    received_at: datetime
    agent_version: str
    os: str
    arch: str
    adapter_versions: dict[str, str]
    disk_free_bytes: int
    network_reachable: bool
    current_session_id: UUID | None = None
    spool_pending_count: int
    spool_pending_bytes: int
    upload_queue_depth: int
    last_error: str | None = None

    @classmethod
    def from_report(
        cls, report: HeartbeatReport, received_at: datetime
    ) -> AgentHeartbeatSummaryOut:
        return cls(
            reported_at=report.reported_at,
            received_at=received_at,
            agent_version=report.agent_version,
            os=report.os,
            arch=report.arch,
            adapter_versions=dict(report.adapter_versions),
            disk_free_bytes=report.disk_free_bytes,
            network_reachable=report.network_reachable,
            current_session_id=report.current_session_id,
            spool_pending_count=report.spool_pending_count,
            spool_pending_bytes=report.spool_pending_bytes,
            upload_queue_depth=report.upload_queue_depth,
            last_error=report.last_error,
        )


class AgentLeaseOut(BaseModel):
    lease_id: UUID
    project_id: UUID
    agent_id: UUID
    capture_session_id: UUID
    device_ids: list[UUID]
    status: str
    acquired_at: datetime
    expires_at: datetime
    last_renewed_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None
    release_note: str | None = None

    @classmethod
    def from_domain(cls, lease: Lease, *, now: datetime | None = None) -> AgentLeaseOut:
        ref = now or datetime.now(UTC)
        return cls(
            lease_id=lease.lease_id,
            project_id=lease.project_id,
            agent_id=lease.agent_id,
            capture_session_id=lease.session_id,
            device_ids=list(lease.device_ids),
            status=lease.status(ref).value,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            last_renewed_at=lease.last_renewed_at,
            released_at=lease.released_at,
            release_reason=lease.release_reason.value if lease.release_reason else None,
            release_note=lease.release_note,
        )


class EdgeAgentOut(BaseModel):
    agent_id: UUID
    project_id: UUID
    display_name: str
    robot_id: UUID
    device_ids: list[UUID]
    status: str
    connectivity: str
    dispatch_paused: bool
    agent_version: str | None = None
    os_arch: str | None = None
    token_generation: int
    token_rotated_at: datetime | None = None
    paired_at: datetime
    activated_at: datetime | None = None
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    latest_heartbeat: AgentHeartbeatSummaryOut | None = None

    @classmethod
    def from_view(cls, view: AgentView) -> EdgeAgentOut:
        agent = view.agent
        hb = (
            AgentHeartbeatSummaryOut.from_report(
                view.latest_heartbeat, view.latest_heartbeat_received_at
            )
            if view.latest_heartbeat is not None and view.latest_heartbeat_received_at is not None
            else None
        )
        return cls(
            agent_id=agent.agent_id,
            project_id=agent.project_id,
            display_name=agent.display_name,
            robot_id=agent.robot_id,
            device_ids=list(agent.device_ids),
            status=agent.status.value,
            connectivity=view.connectivity.value,
            dispatch_paused=agent.dispatch_paused,
            agent_version=agent.agent_version,
            os_arch=agent.os_arch,
            token_generation=agent.token_generation,
            token_rotated_at=agent.token_rotated_at,
            paired_at=agent.paired_at,
            activated_at=agent.activated_at,
            last_seen_at=agent.last_seen_at,
            revoked_at=agent.revoked_at,
            revoke_reason=agent.revoke_reason,
            latest_heartbeat=hb,
        )


class EdgeAgentDetailOut(EdgeAgentOut):
    active_leases: list[AgentLeaseOut]

    @classmethod
    def from_detail(cls, detail: AgentDetailView) -> EdgeAgentDetailOut:
        base = EdgeAgentOut.from_view(detail)
        return cls(
            **base.model_dump(),
            active_leases=[AgentLeaseOut.from_domain(item) for item in detail.active_leases],
        )


class AgentRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class AgentDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool
    reason: str | None = Field(default=None, max_length=1000)


class AgentLeaseReclaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class EdgeAgentTokenRotatedOut(BaseModel):
    agent_id: UUID
    token_generation: int
    token_rotated_at: datetime
    agent_token: str

    @classmethod
    def from_result(cls, result: RotatedAgentToken) -> EdgeAgentTokenRotatedOut:
        assert result.agent.token_rotated_at is not None
        return cls(
            agent_id=result.agent.agent_id,
            token_generation=result.agent.token_generation,
            token_rotated_at=result.agent.token_rotated_at,
            agent_token=result.agent_token,
        )


class AgentPairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_token: str = Field(min_length=1, max_length=128)
    agent_version: str | None = Field(default=None, max_length=64)
    os: str | None = Field(default=None, max_length=64)
    arch: str | None = Field(default=None, max_length=32)


class AgentPairResponse(BaseModel):
    agent_id: UUID
    project_id: UUID
    robot_id: UUID
    display_name: str
    device_ids: list[UUID]
    agent_token: str
    heartbeat_interval_seconds: int
    lease_ttl_seconds: int
    capture_grace_seconds: int

    @classmethod
    def from_result(cls, result: PairedAgentResult) -> AgentPairResponse:
        return cls(
            agent_id=result.agent.agent_id,
            project_id=result.agent.project_id,
            robot_id=result.agent.robot_id,
            display_name=result.agent.display_name,
            device_ids=list(result.agent.device_ids),
            agent_token=result.agent_token,
            heartbeat_interval_seconds=result.policy.heartbeat_interval_seconds,
            lease_ttl_seconds=result.policy.lease_ttl_seconds,
            capture_grace_seconds=result.policy.capture_grace_seconds,
        )


class AgentHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reported_at: datetime
    version: str = Field(min_length=1, max_length=64)
    os: str = Field(min_length=1, max_length=64)
    arch: str = Field(min_length=1, max_length=32)
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    disk_free_bytes: int = Field(ge=0)
    network_reachable: bool
    current_session_id: UUID | None = None
    spool_pending_count: int = Field(ge=0)
    spool_pending_bytes: int = Field(ge=0)
    upload_queue_depth: int = Field(ge=0)
    last_error: str | None = Field(default=None, max_length=1000)
    renew_lease_ids: list[UUID] | None = None


class AgentHeartbeatResponse(BaseModel):
    agent_id: UUID
    status: str
    received_at: datetime
    dispatch_paused: bool
    renewed_leases: list[AgentLeaseOut]
    expired_lease_ids: list[UUID]

    @classmethod
    def from_result(cls, result: HeartbeatResult) -> AgentHeartbeatResponse:
        return cls(
            agent_id=result.agent.agent_id,
            status=result.agent.status.value,
            received_at=result.received_at,
            dispatch_paused=result.agent.dispatch_paused,
            renewed_leases=[
                AgentLeaseOut.from_domain(item, now=result.received_at)
                for item in result.renewed_leases
            ],
            expired_lease_ids=list(result.expired_lease_ids),
        )


class AgentLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["acquire", "release"] = "acquire"
    capture_session_id: UUID | None = None
    device_ids: list[UUID] | None = Field(default=None, max_length=64)
    lease_id: UUID | None = None
    release_reason: str | None = Field(default=None, max_length=32)
    release_note: str | None = Field(default=None, max_length=500)


class AgentAssignmentOut(BaseModel):
    capture_session_id: UUID
    code: str
    status: str
    task_id: UUID | None = None
    batch_id: UUID | None = None
    robot_id: UUID | None = None
    location_id: UUID | None = None
    active_lease_id: UUID | None = None

    @classmethod
    def from_item(cls, item: AssignmentItem) -> AgentAssignmentOut:
        return cls(
            capture_session_id=item.capture_session_id,
            code=item.code,
            status=item.status,
            task_id=item.task_id,
            batch_id=item.batch_id,
            robot_id=item.robot_id,
            location_id=item.location_id,
            active_lease_id=item.active_lease_id,
        )


class AgentAssignmentListOut(BaseModel):
    agent_id: UUID
    dispatch_paused: bool
    items: list[AgentAssignmentOut]

    @classmethod
    def from_result(cls, result: AssignmentListResult) -> AgentAssignmentListOut:
        return cls(
            agent_id=result.agent_id,
            dispatch_paused=result.dispatch_paused,
            items=[AgentAssignmentOut.from_item(item) for item in result.items],
        )
