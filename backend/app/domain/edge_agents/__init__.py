"""Edge Agent 域：配对身份、心跳、任务 Lease（V31-011 / ADR-0047）。

纯 Python 规则，不依赖 Web / ORM / 队列。
"""

from app.domain.edge_agents.enums import (
    AGENT_RELEASE_REASONS,
    AGENT_TRANSITIONS,
    AgentStatus,
    Connectivity,
    LeaseReleaseReason,
    LeaseStatus,
    agent_transition_pairs,
)
from app.domain.edge_agents.errors import (
    AgentAuthenticationError,
    AgentScopeError,
    DomainValidationError,
    EdgeAgentDomainError,
    LeaseConflictError,
    ResourceNotFoundError,
    StateTransitionError,
)
from app.domain.edge_agents.heartbeat import HeartbeatReport
from app.domain.edge_agents.lease import Lease
from app.domain.edge_agents.models import EdgeAgent, PairingToken
from app.domain.edge_agents.policy import EdgeAgentPolicy

__all__ = [
    "AGENT_RELEASE_REASONS",
    "AGENT_TRANSITIONS",
    "AgentAuthenticationError",
    "AgentScopeError",
    "AgentStatus",
    "Connectivity",
    "DomainValidationError",
    "EdgeAgent",
    "EdgeAgentDomainError",
    "EdgeAgentPolicy",
    "HeartbeatReport",
    "Lease",
    "LeaseConflictError",
    "LeaseReleaseReason",
    "LeaseStatus",
    "PairingToken",
    "ResourceNotFoundError",
    "StateTransitionError",
    "agent_transition_pairs",
]
