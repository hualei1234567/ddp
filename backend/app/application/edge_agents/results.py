"""Edge Agent 应用层结果视图 DTO。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.edge_agents import (
    Connectivity,
    EdgeAgent,
    EdgeAgentPolicy,
    HeartbeatReport,
    Lease,
    PairingToken,
)


@dataclass(frozen=True, slots=True)
class IssuedPairingToken:
    token: PairingToken
    pairing_token: str


@dataclass(frozen=True, slots=True)
class RotatedAgentToken:
    agent: EdgeAgent
    agent_token: str


@dataclass(frozen=True, slots=True)
class AgentView:
    agent: EdgeAgent
    connectivity: Connectivity
    latest_heartbeat: HeartbeatReport | None
    latest_heartbeat_received_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentDetailView(AgentView):
    active_leases: tuple[Lease, ...]


@dataclass(frozen=True, slots=True)
class PairedAgentResult:
    agent: EdgeAgent
    agent_token: str
    policy: EdgeAgentPolicy


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    agent: EdgeAgent
    received_at: datetime
    renewed_leases: tuple[Lease, ...]
    expired_lease_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AssignmentItem:
    capture_session_id: UUID
    code: str
    status: str
    task_id: UUID | None
    batch_id: UUID | None
    robot_id: UUID | None
    location_id: UUID | None
    active_lease_id: UUID | None


@dataclass(frozen=True, slots=True)
class AssignmentListResult:
    agent_id: UUID
    dispatch_paused: bool
    items: tuple[AssignmentItem, ...]
