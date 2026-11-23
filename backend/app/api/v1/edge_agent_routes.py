"""Edge Agent HTTP 路由：管理面（用户 RBAC）与 Agent 纯拉取面（Agent 专属 Token）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_scope, request_id
from app.api.v1.edge_agent_dependencies import (
    AuthenticatedAgent,
    EdgeAgentAdminDep,
    EdgeAgentProtocolDep,
    ManageEdgeAgentsScope,
)
from app.api.v1.edge_agent_schemas import (
    AgentAssignmentListOut,
    AgentDispatchRequest,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentLeaseOut,
    AgentLeaseReclaimRequest,
    AgentLeaseRequest,
    AgentPairRequest,
    AgentPairResponse,
    AgentRevokeRequest,
    EdgeAgentDetailOut,
    EdgeAgentOut,
    EdgeAgentTokenRotatedOut,
    PairingTokenCreate,
    PairingTokenIssuedOut,
)
from app.api.v1.schemas import PageOut
from app.domain.edge_agents import DomainValidationError, HeartbeatReport
from app.domain.identity.models import ProjectScope

admin_router = APIRouter(prefix="/projects/{project_id}/edge-agents", tags=["edge-agents"])
agent_router = APIRouter(prefix="/edge-agents", tags=["edge-agents"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]


@admin_router.post("/pairing-tokens", response_model=PairingTokenIssuedOut, status_code=201)
def issue_pairing_token(
    payload: PairingTokenCreate,
    request: Request,
    scope: ManageEdgeAgentsScope,
    service: EdgeAgentAdminDep,
) -> PairingTokenIssuedOut:
    result = service.issue_pairing_token(
        scope,
        robot_id=payload.robot_id,
        display_name=payload.display_name,
        device_ids=tuple(payload.device_ids),
        ttl_seconds=payload.ttl_seconds,
        request_id=request_id(request),
    )
    return PairingTokenIssuedOut.from_result(result)


@admin_router.get("", response_model=PageOut[EdgeAgentOut])
def list_edge_agents(
    scope: ReadScope,
    service: EdgeAgentAdminDep,
    status: str | None = None,
    robot_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[EdgeAgentOut]:
    page = service.list_agents(
        scope,
        status=status,
        robot_id=robot_id,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[EdgeAgentOut.from_view(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@admin_router.get("/{agent_id}", response_model=EdgeAgentDetailOut)
def get_edge_agent(
    agent_id: UUID,
    scope: ReadScope,
    service: EdgeAgentAdminDep,
) -> EdgeAgentDetailOut:
    return EdgeAgentDetailOut.from_detail(service.get_agent_detail(scope, agent_id))


@admin_router.post("/{agent_id}/revoke", response_model=EdgeAgentOut)
def revoke_edge_agent(
    agent_id: UUID,
    payload: AgentRevokeRequest,
    request: Request,
    scope: ManageEdgeAgentsScope,
    service: EdgeAgentAdminDep,
) -> EdgeAgentOut:
    view = service.revoke_agent(
        scope,
        agent_id,
        reason=payload.reason,
        request_id=request_id(request),
    )
    return EdgeAgentOut.from_view(view)


@admin_router.post("/{agent_id}/dispatch", response_model=EdgeAgentOut)
def update_edge_agent_dispatch(
    agent_id: UUID,
    payload: AgentDispatchRequest,
    request: Request,
    scope: ManageEdgeAgentsScope,
    service: EdgeAgentAdminDep,
) -> EdgeAgentOut:
    view = service.set_dispatch_paused(
        scope,
        agent_id,
        paused=payload.paused,
        reason=payload.reason,
        request_id=request_id(request),
    )
    return EdgeAgentOut.from_view(view)


@admin_router.post("/{agent_id}/rotate-token", response_model=EdgeAgentTokenRotatedOut)
def rotate_edge_agent_token(
    agent_id: UUID,
    request: Request,
    scope: ManageEdgeAgentsScope,
    service: EdgeAgentAdminDep,
) -> EdgeAgentTokenRotatedOut:
    result = service.rotate_token(scope, agent_id, request_id=request_id(request))
    return EdgeAgentTokenRotatedOut.from_result(result)


@admin_router.post("/{agent_id}/leases/{lease_id}/reclaim", response_model=AgentLeaseOut)
def reclaim_edge_agent_lease(
    agent_id: UUID,
    lease_id: UUID,
    payload: AgentLeaseReclaimRequest,
    request: Request,
    scope: ManageEdgeAgentsScope,
    service: EdgeAgentAdminDep,
) -> AgentLeaseOut:
    lease = service.reclaim_lease(
        scope,
        agent_id,
        lease_id,
        reason=payload.reason,
        request_id=request_id(request),
    )
    return AgentLeaseOut.from_domain(lease)


@agent_router.post("/pair", response_model=AgentPairResponse, status_code=201)
def pair_edge_agent(
    payload: AgentPairRequest,
    service: EdgeAgentProtocolDep,
) -> AgentPairResponse:
    result = service.pair(
        pairing_token=payload.pairing_token,
        agent_version=payload.agent_version,
        os_name=payload.os,
        arch=payload.arch,
    )
    return AgentPairResponse.from_result(result)


@agent_router.post("/{agent_id}/heartbeat", response_model=AgentHeartbeatResponse)
def post_edge_agent_heartbeat(
    payload: AgentHeartbeatRequest,
    agent: AuthenticatedAgent,
    service: EdgeAgentProtocolDep,
) -> AgentHeartbeatResponse:
    report = HeartbeatReport(
        reported_at=payload.reported_at,
        agent_version=payload.version,
        os=payload.os,
        arch=payload.arch,
        network_reachable=payload.network_reachable,
        disk_free_bytes=payload.disk_free_bytes,
        spool_pending_count=payload.spool_pending_count,
        spool_pending_bytes=payload.spool_pending_bytes,
        upload_queue_depth=payload.upload_queue_depth,
        adapter_versions=payload.adapter_versions,
        current_session_id=payload.current_session_id,
        last_error=payload.last_error,
    )
    result = service.record_heartbeat(
        agent,
        report,
        renew_lease_ids=(
            tuple(payload.renew_lease_ids) if payload.renew_lease_ids is not None else None
        ),
    )
    return AgentHeartbeatResponse.from_result(result)


@agent_router.post("/{agent_id}/leases", response_model=AgentLeaseOut)
def manage_edge_agent_lease(
    payload: AgentLeaseRequest,
    agent: AuthenticatedAgent,
    service: EdgeAgentProtocolDep,
) -> AgentLeaseOut:
    if payload.action == "acquire":
        if payload.capture_session_id is None:
            raise DomainValidationError("申请 Lease 必须提供 capture_session_id")
        lease = service.acquire_lease(
            agent,
            capture_session_id=payload.capture_session_id,
            device_ids=tuple(payload.device_ids) if payload.device_ids is not None else None,
        )
        return AgentLeaseOut.from_domain(lease)
    if not payload.release_reason:
        raise DomainValidationError("释放 Lease 必须提供 release_reason")
    lease = service.release_lease(
        agent,
        lease_id=payload.lease_id,
        capture_session_id=payload.capture_session_id,
        reason=payload.release_reason,
        note=payload.release_note,
    )
    return AgentLeaseOut.from_domain(lease)


@agent_router.get("/{agent_id}/assignments", response_model=AgentAssignmentListOut)
def get_edge_agent_assignments(
    agent: AuthenticatedAgent,
    service: EdgeAgentProtocolDep,
) -> AgentAssignmentListOut:
    return AgentAssignmentListOut.from_result(service.list_assignments(agent))
