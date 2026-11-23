"""Edge Agent API 依赖装配与 Agent 专属鉴权（与用户 JWT 完全分离）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request

from app.api.deps import DbSession, extract_bearer_token, get_scope
from app.application.edge_agents import EdgeAgentAdminService, EdgeAgentProtocolService
from app.core.config import Settings, get_settings
from app.domain.edge_agents import AgentAuthenticationError, EdgeAgent, EdgeAgentPolicy
from app.domain.identity.models import ProjectAction, ProjectScope
from app.infrastructure.repositories.collection_audit import SqlAuditRepository
from app.infrastructure.repositories.edge_agents import SqlEdgeAgentRepository


def build_edge_agent_policy(settings: Settings) -> EdgeAgentPolicy:
    return EdgeAgentPolicy(
        heartbeat_interval_seconds=settings.edge_agent_heartbeat_interval_seconds,
        offline_after_seconds=settings.edge_agent_offline_after_seconds,
        lease_ttl_seconds=settings.edge_agent_lease_ttl_seconds,
        capture_grace_seconds=settings.edge_agent_capture_grace_seconds,
        pairing_ttl_default_seconds=settings.edge_agent_pairing_ttl_default_seconds,
        pairing_ttl_max_seconds=settings.edge_agent_pairing_ttl_max_seconds,
        heartbeat_retention_hours=settings.edge_agent_heartbeat_retention_hours,
    )


def get_edge_agent_admin_service(
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EdgeAgentAdminService:
    return EdgeAgentAdminService(
        repo=SqlEdgeAgentRepository(session),
        uow=session,
        audit=SqlAuditRepository(session),
        policy=build_edge_agent_policy(settings),
    )


def get_edge_agent_protocol_service(
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EdgeAgentProtocolService:
    return EdgeAgentProtocolService(
        repo=SqlEdgeAgentRepository(session),
        uow=session,
        policy=build_edge_agent_policy(settings),
    )


def get_authenticated_agent(
    request: Request,
    agent_id: Annotated[UUID, Path(description="Edge Agent ID")],
    service: Annotated[EdgeAgentProtocolService, Depends(get_edge_agent_protocol_service)],
) -> EdgeAgent:
    """Agent 专属鉴权：仅接受 ``ddp_agt_`` 前缀的 Bearer 令牌，绝不回退到用户 JWT。"""
    token = extract_bearer_token(request)
    if token is None:
        raise AgentAuthenticationError("缺少 Agent 访问令牌")
    return service.authenticate_agent(token, path_agent_id=agent_id)


EdgeAgentAdminDep = Annotated[EdgeAgentAdminService, Depends(get_edge_agent_admin_service)]
EdgeAgentProtocolDep = Annotated[EdgeAgentProtocolService, Depends(get_edge_agent_protocol_service)]
ManageEdgeAgentsScope = Annotated[
    ProjectScope, Depends(get_scope(ProjectAction.MANAGE_EDGE_AGENTS))
]
AuthenticatedAgent = Annotated[EdgeAgent, Depends(get_authenticated_agent)]
