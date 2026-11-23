"""Edge Agent 纯拉取协议面应用用例：配对、鉴权、心跳续租、Lease 申请/释放、拉取任务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.collection.ports import UnitOfWork
from app.application.edge_agents.results import (
    AssignmentItem,
    AssignmentListResult,
    HeartbeatResult,
    PairedAgentResult,
)
from app.domain.edge_agents import (
    AGENT_RELEASE_REASONS,
    AgentAuthenticationError,
    AgentScopeError,
    DomainValidationError,
    EdgeAgent,
    EdgeAgentPolicy,
    HeartbeatReport,
    Lease,
    LeaseConflictError,
    LeaseReleaseReason,
    ResourceNotFoundError,
    StateTransitionError,
)
from app.infrastructure.auth.agent_tokens import (
    AGENT_TOKEN_PREFIX,
    PAIRING_TOKEN_PREFIX,
    generate_agent_token,
    has_prefix,
    hash_token,
)
from app.infrastructure.repositories.edge_agents import SqlEdgeAgentRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EdgeAgentProtocolService:
    """Agent 纯拉取协议面用例：独立于用户 JWT。"""

    def __init__(
        self,
        repo: SqlEdgeAgentRepository,
        uow: UnitOfWork,
        policy: EdgeAgentPolicy,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repo = repo
        self._uow = uow
        self._policy = policy
        self._clock = clock

    def authenticate_agent(self, raw_token: str, *, path_agent_id: UUID) -> EdgeAgent:
        if not has_prefix(raw_token, AGENT_TOKEN_PREFIX):
            raise AgentAuthenticationError("无效或已过期的 Agent 令牌")
        agent = self._repo.get_agent_by_token_hash(hash_token(raw_token))
        if agent is None or agent.is_revoked:
            raise AgentAuthenticationError("无效或已过期的 Agent 令牌")
        if agent.agent_id != path_agent_id:
            raise AgentScopeError("Agent 令牌无权访问其他 Agent 的资源")
        return agent

    def pair(
        self,
        *,
        pairing_token: str,
        agent_version: str | None = None,
        os_name: str | None = None,
        arch: str | None = None,
    ) -> PairedAgentResult:
        if not has_prefix(pairing_token, PAIRING_TOKEN_PREFIX):
            raise AgentAuthenticationError("配对凭证无效或已过期")
        record = self._repo.get_pairing_token_by_hash(hash_token(pairing_token))
        if record is None:
            raise AgentAuthenticationError("配对凭证无效或已过期")
        now = self._clock()
        os_arch = f"{os_name}/{arch}" if os_name and arch else (os_name or arch)
        agent_id = uuid4()
        agent = EdgeAgent.from_pairing(
            record,
            agent_id=agent_id,
            now=now,
            agent_version=agent_version,
            os_arch=os_arch,
        )
        raw_agent_token = generate_agent_token()
        self._repo.add_agent(agent, token_hash=hash_token(raw_agent_token))
        if not self._repo.consume_pairing_token(
            record.token_id,
            agent_id=agent_id,
            consumed_at=now,
        ):
            self._uow.rollback()
            raise AgentAuthenticationError("配对凭证无效或已过期")
        self._uow.commit()
        return PairedAgentResult(
            agent=agent,
            agent_token=raw_agent_token,
            policy=self._policy,
        )

    def record_heartbeat(
        self,
        agent: EdgeAgent,
        report: HeartbeatReport,
        *,
        renew_lease_ids: tuple[UUID, ...] | None = None,
    ) -> HeartbeatResult:
        if report.current_session_id is not None:
            session = self._repo.get_capture_session(agent.project_id, report.current_session_id)
            if session is None:
                raise DomainValidationError("current_session_id 不存在或不属于当前项目")
        now = self._clock()
        os_arch = f"{report.os}/{report.arch}"
        updated = agent.record_heartbeat(now, report.agent_version, os_arch=os_arch)
        self._repo.save_agent(updated, now=now)
        self._repo.upsert_heartbeat(updated, report, received_at=now)
        renewed, expired = self._renew_leases(updated, now, renew_lease_ids)
        self._uow.commit()
        return HeartbeatResult(
            agent=updated,
            received_at=now,
            renewed_leases=tuple(renewed),
            expired_lease_ids=tuple(expired),
        )

    def acquire_lease(
        self,
        agent: EdgeAgent,
        *,
        capture_session_id: UUID,
        device_ids: tuple[UUID, ...] | None = None,
    ) -> Lease:
        agent.ensure_can_hold_lease()
        session = self._repo.get_capture_session(agent.project_id, capture_session_id)
        if session is None:
            raise ResourceNotFoundError("采集会话")
        if session.robot_id != agent.robot_id:
            raise AgentScopeError("该采集会话未绑定到此 Agent 的机器人")
        if session.status not in {"SCHEDULED", "RUNNING", "PAUSED"}:
            raise StateTransitionError("采集会话已终态，不能申请 Lease")
        scoped_devices = self._resolve_lease_devices(agent, device_ids)
        now = self._clock()
        existing = self._repo.get_unreleased_lease_for_session(agent.project_id, capture_session_id)
        if existing is not None:
            if existing.is_active(now):
                raise LeaseConflictError("该采集会话已有其他 active Lease")
            expired = existing.release(
                now,
                reason=LeaseReleaseReason.EXPIRED,
                note="expired_before_reacquire",
            )
            self._repo.save_lease(expired)
        lease = Lease.acquire(
            lease_id=uuid4(),
            project_id=agent.project_id,
            agent_id=agent.agent_id,
            session_id=capture_session_id,
            device_ids=scoped_devices,
            now=now,
            ttl_seconds=self._policy.lease_ttl_seconds,
        )
        self._repo.add_lease(lease)
        self._uow.commit()
        return lease

    def release_lease(
        self,
        agent: EdgeAgent,
        *,
        lease_id: UUID | None,
        capture_session_id: UUID | None,
        reason: str,
        note: str | None = None,
    ) -> Lease:
        try:
            parsed_reason = LeaseReleaseReason(reason)
        except ValueError as exc:
            raise DomainValidationError("无效的 Lease 释放原因") from exc
        if parsed_reason not in AGENT_RELEASE_REASONS:
            raise DomainValidationError("Agent 只能声明 COMPLETED / RELEASED_BY_AGENT / FAILED")
        lease = self._find_lease_for_release(agent, lease_id, capture_session_id)
        if lease.agent_id != agent.agent_id:
            raise AgentScopeError("无权释放其他 Agent 持有的 Lease")
        now = self._clock()
        released = lease.release(now, reason=parsed_reason, note=note)
        self._repo.save_lease(released)
        self._uow.commit()
        return released

    def list_assignments(self, agent: EdgeAgent) -> AssignmentListResult:
        if agent.dispatch_paused:
            return AssignmentListResult(
                agent_id=agent.agent_id,
                dispatch_paused=True,
                items=(),
            )
        now = self._clock()
        rows = self._repo.list_assignable_sessions(
            agent.project_id,
            agent.robot_id,
            agent.agent_id,
            now=now,
        )
        items = tuple(
            AssignmentItem(
                capture_session_id=session.id,
                code=session.code,
                status=session.status,
                task_id=session.task_id,
                batch_id=session.batch_id,
                robot_id=session.robot_id,
                location_id=session.location_id,
                active_lease_id=active_lease_id,
            )
            for session, active_lease_id in rows
        )
        return AssignmentListResult(
            agent_id=agent.agent_id,
            dispatch_paused=False,
            items=items,
        )

    def _renew_leases(
        self,
        agent: EdgeAgent,
        now: datetime,
        renew_lease_ids: tuple[UUID, ...] | None,
    ) -> tuple[list[Lease], list[UUID]]:
        unreleased = self._repo.list_unreleased_leases_for_agent(agent.project_id, agent.agent_id)
        target_ids = set(renew_lease_ids) if renew_lease_ids is not None else None
        renewed: list[Lease] = []
        expired: list[UUID] = []
        for lease in unreleased:
            if target_ids is not None and lease.lease_id not in target_ids:
                continue
            if lease.is_active(now):
                next_lease = lease.renew(now, ttl_seconds=self._policy.lease_ttl_seconds)
                self._repo.save_lease(next_lease)
                renewed.append(next_lease)
            else:
                expired.append(lease.lease_id)
        return renewed, expired

    def _resolve_lease_devices(
        self,
        agent: EdgeAgent,
        requested: tuple[UUID, ...] | None,
    ) -> tuple[UUID, ...]:
        devices = agent.device_ids if requested is None else requested
        if agent.device_ids and not set(devices).issubset(set(agent.device_ids)):
            raise AgentScopeError("请求的 device_ids 超出 Agent 授权设备范围")
        if devices and not self._repo.verify_devices_in_project(agent.project_id, devices):
            raise DomainValidationError("device_ids 包含不存在或不属于当前项目的设备")
        return devices

    def _find_lease_for_release(
        self,
        agent: EdgeAgent,
        lease_id: UUID | None,
        capture_session_id: UUID | None,
    ) -> Lease:
        if lease_id is not None:
            lease = self._repo.get_lease(agent.project_id, lease_id)
        elif capture_session_id is not None:
            lease = self._repo.get_unreleased_lease_for_session(
                agent.project_id, capture_session_id
            )
        else:
            raise DomainValidationError("释放 Lease 必须提供 lease_id 或 capture_session_id")
        if lease is None:
            raise ResourceNotFoundError("任务 Lease")
        return lease
