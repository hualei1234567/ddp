"""Edge Agent 管理面应用用例：配对凭证、查询、撤销、派发开关、轮换与 Lease 强回收。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import AuditRecord, AuditRepository, UnitOfWork
from app.application.edge_agents.results import (
    AgentDetailView,
    AgentView,
    IssuedPairingToken,
    RotatedAgentToken,
)
from app.domain.collection.errors import CollectionDomainError
from app.domain.edge_agents import (
    AgentStatus,
    DomainValidationError,
    EdgeAgent,
    EdgeAgentPolicy,
    Lease,
    LeaseReleaseReason,
    PairingToken,
    ResourceNotFoundError,
)
from app.domain.edge_agents.models import clean_reason
from app.domain.identity.models import ProjectScope
from app.infrastructure.auth.agent_tokens import (
    generate_agent_token,
    generate_pairing_token,
    hash_token,
)
from app.infrastructure.repositories.edge_agents import SqlEdgeAgentRepository

_AGENT_RESOURCE = "EDGE_AGENT"
_LEASE_RESOURCE = "AGENT_TASK_LEASE"
_PAIRING_RESOURCE = "EDGE_AGENT_PAIRING_TOKEN"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_agent_status(raw: str | None) -> AgentStatus | None:
    if raw is None:
        return None
    try:
        return AgentStatus(raw)
    except ValueError as exc:
        raise DomainValidationError("无效的 Agent 状态过滤值") from exc


class EdgeAgentAdminService:
    """管理员面用例：依赖 User JWT + ProjectScope + RBAC。"""

    def __init__(
        self,
        repo: SqlEdgeAgentRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        policy: EdgeAgentPolicy,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repo = repo
        self._uow = uow
        self._audit = audit
        self._policy = policy
        self._clock = clock

    def issue_pairing_token(
        self,
        scope: ProjectScope,
        *,
        robot_id: UUID,
        display_name: str,
        device_ids: tuple[UUID, ...] = (),
        ttl_seconds: int | None = None,
        request_id: str | None = None,
    ) -> IssuedPairingToken:
        if not self._repo.verify_robot_in_project(scope.project_id, robot_id):
            raise ResourceNotFoundError("机器人")
        if device_ids and not self._repo.verify_devices_in_project(scope.project_id, device_ids):
            raise DomainValidationError("device_ids 包含不存在或不属于当前项目的设备")
        now = self._clock()
        token = PairingToken.issue(
            token_id=uuid4(),
            project_id=scope.project_id,
            robot_id=robot_id,
            display_name=display_name,
            created_by=scope.actor_id,
            now=now,
            ttl_seconds=ttl_seconds,
            policy=self._policy,
            device_ids=device_ids,
        )
        raw_token = generate_pairing_token()
        self._repo.add_pairing_token(token, token_hash=hash_token(raw_token))
        self._record_audit(
            scope,
            resource_type=_PAIRING_RESOURCE,
            resource_id=token.token_id,
            action="issue",
            to_status="ISSUED",
            request_id=request_id,
            details={"robot_id": str(robot_id), "device_count": len(device_ids)},
            occurred_at=now,
        )
        self._uow.commit()
        return IssuedPairingToken(token=token, pairing_token=raw_token)

    def list_agents(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        robot_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[AgentView]:
        parsed_status = _parse_agent_status(status)
        try:
            page_limit = normalize_limit(limit)
            after_id = decode_cursor(cursor)
        except CollectionDomainError as exc:
            raise DomainValidationError(str(exc)) from exc
        rows = self._repo.list_agents(
            scope,
            status=parsed_status,
            robot_id=robot_id,
            after_id=after_id,
            limit=page_limit + 1,
        )
        page = build_page(rows, limit=page_limit, id_of=lambda item: item.agent_id)
        heartbeats = self._repo.get_latest_heartbeats([item.agent_id for item in page.items])
        now = self._clock()
        views = [
            AgentView(
                agent=item,
                connectivity=item.connectivity(now, self._policy),
                latest_heartbeat=heartbeats[item.agent_id][0]
                if item.agent_id in heartbeats
                else None,
                latest_heartbeat_received_at=heartbeats[item.agent_id][1]
                if item.agent_id in heartbeats
                else None,
            )
            for item in page.items
        ]
        return Page(items=views, next_cursor=page.next_cursor)

    def get_agent_detail(self, scope: ProjectScope, agent_id: UUID) -> AgentDetailView:
        agent = self._require_agent(scope, agent_id)
        now = self._clock()
        latest = self._repo.get_latest_heartbeat(agent.agent_id)
        unreleased = self._repo.list_unreleased_leases_for_agent(scope.project_id, agent.agent_id)
        return AgentDetailView(
            agent=agent,
            connectivity=agent.connectivity(now, self._policy),
            latest_heartbeat=latest[0] if latest else None,
            latest_heartbeat_received_at=latest[1] if latest else None,
            active_leases=tuple(unreleased),
        )

    def revoke_agent(
        self,
        scope: ProjectScope,
        agent_id: UUID,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> AgentView:
        agent = self._require_agent(scope, agent_id)
        now = self._clock()
        revoked = agent.revoke(now, actor_id=scope.actor_id, reason=reason)
        self._repo.save_agent(revoked, now=now)
        released_count = self._repo.release_all_agent_leases(
            scope.project_id,
            agent.agent_id,
            now=now,
            reason=LeaseReleaseReason.AGENT_REVOKED,
            note=revoked.revoke_reason,
        )
        self._record_audit(
            scope,
            resource_type=_AGENT_RESOURCE,
            resource_id=agent.agent_id,
            action="revoke",
            from_status=agent.status.value,
            to_status=revoked.status.value,
            reason=revoked.revoke_reason,
            request_id=request_id,
            details={"released_leases": released_count},
            occurred_at=now,
        )
        self._uow.commit()
        return self._build_view(revoked, now)

    def set_dispatch_paused(
        self,
        scope: ProjectScope,
        agent_id: UUID,
        *,
        paused: bool,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> AgentView:
        agent = self._require_agent(scope, agent_id)
        now = self._clock()
        updated = agent.set_dispatch_paused(paused)
        cleaned_reason = clean_reason(reason, required=False)
        self._repo.save_agent(updated, now=now)
        self._record_audit(
            scope,
            resource_type=_AGENT_RESOURCE,
            resource_id=agent.agent_id,
            action="pause_dispatch" if paused else "resume_dispatch",
            from_status=agent.status.value,
            to_status=updated.status.value,
            reason=cleaned_reason,
            request_id=request_id,
            details={"dispatch_paused": paused},
            occurred_at=now,
        )
        self._uow.commit()
        return self._build_view(updated, now)

    def rotate_token(
        self,
        scope: ProjectScope,
        agent_id: UUID,
        *,
        request_id: str | None = None,
    ) -> RotatedAgentToken:
        agent = self._require_agent(scope, agent_id)
        now = self._clock()
        rotated = agent.rotate_token(now)
        raw_token = generate_agent_token()
        self._repo.save_agent(rotated, now=now, new_token_hash=hash_token(raw_token))
        self._record_audit(
            scope,
            resource_type=_AGENT_RESOURCE,
            resource_id=agent.agent_id,
            action="rotate_token",
            from_status=agent.status.value,
            to_status=rotated.status.value,
            request_id=request_id,
            details={"token_generation": rotated.token_generation},
            occurred_at=now,
        )
        self._uow.commit()
        return RotatedAgentToken(agent=rotated, agent_token=raw_token)

    def reclaim_lease(
        self,
        scope: ProjectScope,
        agent_id: UUID,
        lease_id: UUID,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> Lease:
        self._require_agent(scope, agent_id)
        lease = self._repo.get_lease(scope.project_id, lease_id)
        if lease is None or lease.agent_id != agent_id:
            raise ResourceNotFoundError("任务 Lease")
        cleaned_reason = clean_reason(reason, required=True)
        now = self._clock()
        released = lease.release(
            now,
            reason=LeaseReleaseReason.ADMIN_RECLAIMED,
            note=cleaned_reason,
        )
        self._repo.save_lease(released)
        self._record_audit(
            scope,
            resource_type=_LEASE_RESOURCE,
            resource_id=lease.lease_id,
            action="reclaim",
            from_status=lease.status(now).value,
            to_status="RELEASED",
            reason=cleaned_reason,
            request_id=request_id,
            details={
                "agent_id": str(agent_id),
                "capture_session_id": str(lease.session_id),
            },
            occurred_at=now,
        )
        self._uow.commit()
        return released

    def _require_agent(self, scope: ProjectScope, agent_id: UUID) -> EdgeAgent:
        agent = self._repo.get_agent(scope, agent_id)
        if agent is None:
            raise ResourceNotFoundError("Edge Agent")
        return agent

    def _build_view(self, agent: EdgeAgent, now: datetime) -> AgentView:
        latest = self._repo.get_latest_heartbeat(agent.agent_id)
        return AgentView(
            agent=agent,
            connectivity=agent.connectivity(now, self._policy),
            latest_heartbeat=latest[0] if latest else None,
            latest_heartbeat_received_at=latest[1] if latest else None,
        )

    def _record_audit(
        self,
        scope: ProjectScope,
        *,
        resource_type: str,
        resource_id: UUID,
        action: str,
        to_status: str,
        occurred_at: datetime,
        from_status: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self._audit.record(
            scope,
            AuditRecord(
                audit_id=uuid4(),
                project_id=scope.project_id,
                actor_user_id=scope.actor_id,
                actor_role="owner",
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                request_id=request_id,
                result="SUCCESS",
                details=details or {},
                occurred_at=occurred_at,
            ),
        )
