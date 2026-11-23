"""Edge Agent SQLAlchemy 仓储（V31-011）。

所有管理面读取强制带 ``project_id`` 过滤；Agent 面操作只操作已认证 Agent 所属项目。
凭据明文不进入仓储，只存储与查询 SHA-256 摘要。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.edge_agents import (
    AgentStatus,
    DomainValidationError,
    EdgeAgent,
    HeartbeatReport,
    Lease,
    LeaseConflictError,
    LeaseReleaseReason,
    PairingToken,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.capture_session import CaptureSessionModel
from app.infrastructure.db.models.devices import Device, Robot
from app.infrastructure.db.models.edge_agents import (
    AgentHeartbeatModel,
    AgentTaskLeaseModel,
    EdgeAgentModel,
    EdgeAgentPairingTokenModel,
)
from app.infrastructure.repositories._edge_agent_rows import (
    agent_from_row,
    as_utc_required,
    heartbeat_from_row,
    lease_from_row,
    pairing_from_row,
)

_ASSIGNABLE_SESSION_STATUSES = ("SCHEDULED", "RUNNING", "PAUSED")


class SqlEdgeAgentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def verify_robot_in_project(self, project_id: UUID, robot_id: UUID) -> bool:
        row = self._session.scalar(
            select(Robot.id).where(Robot.project_id == project_id, Robot.id == robot_id)
        )
        return row is not None

    def verify_devices_in_project(self, project_id: UUID, device_ids: tuple[UUID, ...]) -> bool:
        if not device_ids:
            return True
        rows = self._session.scalars(
            select(Device.id).where(
                Device.project_id == project_id,
                Device.id.in_(device_ids),
            )
        ).all()
        return len(rows) == len(device_ids)

    def get_capture_session(self, project_id: UUID, session_id: UUID) -> CaptureSessionModel | None:
        return self._session.scalar(
            select(CaptureSessionModel).where(
                CaptureSessionModel.project_id == project_id,
                CaptureSessionModel.id == session_id,
            )
        )

    def add_pairing_token(self, token: PairingToken, *, token_hash: str) -> None:
        self._session.add(
            EdgeAgentPairingTokenModel(
                id=token.token_id,
                project_id=token.project_id,
                robot_id=token.robot_id,
                device_ids=[str(item) for item in token.device_ids],
                display_name=token.display_name,
                token_hash=token_hash,
                created_by=token.created_by,
                created_at=token.created_at,
                expires_at=token.expires_at,
            )
        )
        self._session.flush()

    def get_pairing_token_by_hash(self, token_hash: str) -> PairingToken | None:
        row = self._session.scalar(
            select(EdgeAgentPairingTokenModel).where(
                EdgeAgentPairingTokenModel.token_hash == token_hash
            )
        )
        return pairing_from_row(row) if row is not None else None

    def consume_pairing_token(
        self,
        token_id: UUID,
        *,
        agent_id: UUID,
        consumed_at: datetime,
    ) -> bool:
        result = self._session.execute(
            update(EdgeAgentPairingTokenModel)
            .where(
                EdgeAgentPairingTokenModel.id == token_id,
                EdgeAgentPairingTokenModel.consumed_at.is_(None),
                EdgeAgentPairingTokenModel.expires_at > consumed_at,
            )
            .values(consumed_at=consumed_at, agent_id=agent_id)
        )
        return result.rowcount == 1

    def add_agent(self, agent: EdgeAgent, *, token_hash: str) -> None:
        self._session.add(
            EdgeAgentModel(
                id=agent.agent_id,
                project_id=agent.project_id,
                display_name=agent.display_name,
                robot_id=agent.robot_id,
                device_ids=[str(item) for item in agent.device_ids],
                status=agent.status.value,
                dispatch_paused=agent.dispatch_paused,
                token_hash=token_hash,
                token_generation=agent.token_generation,
                token_rotated_at=agent.token_rotated_at,
                agent_version=agent.agent_version,
                os_arch=agent.os_arch,
                paired_at=agent.paired_at,
                activated_at=agent.activated_at,
                last_seen_at=agent.last_seen_at,
                revoked_at=agent.revoked_at,
                revoked_by=agent.revoked_by,
                revoke_reason=agent.revoke_reason,
                created_by=agent.created_by,
                created_at=agent.created_at,
                updated_at=agent.created_at,
            )
        )
        self._session.flush()

    def get_agent(self, scope: ProjectScope, agent_id: UUID) -> EdgeAgent | None:
        row = self._session.scalar(
            select(EdgeAgentModel).where(
                EdgeAgentModel.project_id == scope.project_id,
                EdgeAgentModel.id == agent_id,
            )
        )
        return agent_from_row(row) if row is not None else None

    def get_agent_by_token_hash(self, token_hash: str) -> EdgeAgent | None:
        row = self._session.scalar(
            select(EdgeAgentModel).where(EdgeAgentModel.token_hash == token_hash)
        )
        return agent_from_row(row) if row is not None else None

    def list_agents(
        self,
        scope: ProjectScope,
        *,
        status: AgentStatus | None = None,
        robot_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[EdgeAgent]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        stmt = select(EdgeAgentModel).where(EdgeAgentModel.project_id == scope.project_id)
        if status is not None:
            stmt = stmt.where(EdgeAgentModel.status == status.value)
        if robot_id is not None:
            stmt = stmt.where(EdgeAgentModel.robot_id == robot_id)
        if after_id is not None:
            stmt = stmt.where(EdgeAgentModel.id > after_id)
        stmt = stmt.order_by(EdgeAgentModel.id).limit(limit)
        return [agent_from_row(row) for row in self._session.scalars(stmt)]

    def save_agent(
        self,
        agent: EdgeAgent,
        *,
        now: datetime,
        new_token_hash: str | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": agent.status.value,
            "dispatch_paused": agent.dispatch_paused,
            "token_generation": agent.token_generation,
            "token_rotated_at": agent.token_rotated_at,
            "agent_version": agent.agent_version,
            "os_arch": agent.os_arch,
            "activated_at": agent.activated_at,
            "last_seen_at": agent.last_seen_at,
            "revoked_at": agent.revoked_at,
            "revoked_by": agent.revoked_by,
            "revoke_reason": agent.revoke_reason,
            "updated_at": now,
        }
        if new_token_hash is not None:
            values["token_hash"] = new_token_hash
        self._session.execute(
            update(EdgeAgentModel)
            .where(
                EdgeAgentModel.project_id == agent.project_id,
                EdgeAgentModel.id == agent.agent_id,
            )
            .values(**values)
        )
        self._session.flush()

    def upsert_heartbeat(
        self,
        agent: EdgeAgent,
        report: HeartbeatReport,
        *,
        received_at: datetime,
    ) -> None:
        existing = self._session.scalar(
            select(AgentHeartbeatModel).where(
                AgentHeartbeatModel.agent_id == agent.agent_id,
                AgentHeartbeatModel.reported_at == report.reported_at,
            )
        )
        if existing is not None:
            existing.received_at = received_at
            existing.agent_version = report.agent_version
            existing.os = report.os
            existing.arch = report.arch
            existing.adapter_versions = dict(report.adapter_versions)
            existing.disk_free_bytes = report.disk_free_bytes
            existing.network_reachable = report.network_reachable
            existing.current_session_id = report.current_session_id
            existing.spool_pending_count = report.spool_pending_count
            existing.spool_pending_bytes = report.spool_pending_bytes
            existing.upload_queue_depth = report.upload_queue_depth
            existing.last_error = report.last_error
        else:
            self._session.add(
                AgentHeartbeatModel(
                    id=uuid4(),
                    project_id=agent.project_id,
                    agent_id=agent.agent_id,
                    received_at=received_at,
                    reported_at=report.reported_at,
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
            )
        self._session.flush()

    def get_latest_heartbeat(self, agent_id: UUID) -> tuple[HeartbeatReport, datetime] | None:
        row = self._session.scalar(
            select(AgentHeartbeatModel)
            .where(AgentHeartbeatModel.agent_id == agent_id)
            .order_by(desc(AgentHeartbeatModel.received_at), desc(AgentHeartbeatModel.reported_at))
            .limit(1)
        )
        if row is None:
            return None
        return heartbeat_from_row(row), as_utc_required(row.received_at)

    def get_latest_heartbeats(
        self, agent_ids: list[UUID]
    ) -> dict[UUID, tuple[HeartbeatReport, datetime]]:
        if not agent_ids:
            return {}
        rows = self._session.scalars(
            select(AgentHeartbeatModel)
            .where(AgentHeartbeatModel.agent_id.in_(agent_ids))
            .order_by(
                AgentHeartbeatModel.agent_id,
                desc(AgentHeartbeatModel.received_at),
                desc(AgentHeartbeatModel.reported_at),
            )
        ).all()
        latest: dict[UUID, tuple[HeartbeatReport, datetime]] = {}
        for row in rows:
            if row.agent_id not in latest:
                latest[row.agent_id] = (
                    heartbeat_from_row(row),
                    as_utc_required(row.received_at),
                )
        return latest

    def add_lease(self, lease: Lease) -> None:
        self._session.add(
            AgentTaskLeaseModel(
                id=lease.lease_id,
                project_id=lease.project_id,
                agent_id=lease.agent_id,
                capture_session_id=lease.session_id,
                device_ids=[str(item) for item in lease.device_ids],
                acquired_at=lease.acquired_at,
                expires_at=lease.expires_at,
                last_renewed_at=lease.last_renewed_at,
                released_at=lease.released_at,
                release_reason=lease.release_reason.value if lease.release_reason else None,
                release_note=lease.release_note,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise LeaseConflictError("该采集会话已存在未释放的 active Lease") from exc

    def get_lease(self, project_id: UUID, lease_id: UUID) -> Lease | None:
        row = self._session.scalar(
            select(AgentTaskLeaseModel).where(
                AgentTaskLeaseModel.project_id == project_id,
                AgentTaskLeaseModel.id == lease_id,
            )
        )
        return lease_from_row(row) if row is not None else None

    def get_unreleased_lease_for_session(self, project_id: UUID, session_id: UUID) -> Lease | None:
        row = self._session.scalar(
            select(AgentTaskLeaseModel).where(
                AgentTaskLeaseModel.project_id == project_id,
                AgentTaskLeaseModel.capture_session_id == session_id,
                AgentTaskLeaseModel.released_at.is_(None),
            )
        )
        return lease_from_row(row) if row is not None else None

    def list_unreleased_leases_for_agent(self, project_id: UUID, agent_id: UUID) -> list[Lease]:
        rows = self._session.scalars(
            select(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.project_id == project_id,
                AgentTaskLeaseModel.agent_id == agent_id,
                AgentTaskLeaseModel.released_at.is_(None),
            )
            .order_by(AgentTaskLeaseModel.acquired_at)
        ).all()
        return [lease_from_row(row) for row in rows]

    def save_lease(self, lease: Lease) -> None:
        self._session.execute(
            update(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.project_id == lease.project_id,
                AgentTaskLeaseModel.id == lease.lease_id,
            )
            .values(
                expires_at=lease.expires_at,
                last_renewed_at=lease.last_renewed_at,
                released_at=lease.released_at,
                release_reason=lease.release_reason.value if lease.release_reason else None,
                release_note=lease.release_note,
            )
        )
        self._session.flush()

    def release_all_agent_leases(
        self,
        project_id: UUID,
        agent_id: UUID,
        *,
        now: datetime,
        reason: LeaseReleaseReason,
        note: str | None,
    ) -> int:
        result = self._session.execute(
            update(AgentTaskLeaseModel)
            .where(
                AgentTaskLeaseModel.project_id == project_id,
                AgentTaskLeaseModel.agent_id == agent_id,
                AgentTaskLeaseModel.released_at.is_(None),
            )
            .values(
                released_at=now,
                release_reason=reason.value,
                release_note=note,
            )
        )
        self._session.flush()
        return int(result.rowcount or 0)

    def list_assignable_sessions(
        self,
        project_id: UUID,
        robot_id: UUID,
        agent_id: UUID,
        *,
        now: datetime,
    ) -> list[tuple[CaptureSessionModel, UUID | None]]:
        sessions = self._session.scalars(
            select(CaptureSessionModel)
            .where(
                CaptureSessionModel.project_id == project_id,
                CaptureSessionModel.robot_id == robot_id,
                CaptureSessionModel.status.in_(_ASSIGNABLE_SESSION_STATUSES),
            )
            .order_by(CaptureSessionModel.created_at, CaptureSessionModel.id)
        ).all()
        if not sessions:
            return []
        session_ids = [item.id for item in sessions]
        leases = self._session.scalars(
            select(AgentTaskLeaseModel).where(
                AgentTaskLeaseModel.project_id == project_id,
                AgentTaskLeaseModel.capture_session_id.in_(session_ids),
                AgentTaskLeaseModel.released_at.is_(None),
            )
        ).all()
        lease_by_session = {item.capture_session_id: item for item in leases}
        result: list[tuple[CaptureSessionModel, UUID | None]] = []
        for item in sessions:
            lease = lease_by_session.get(item.id)
            if lease is None:
                result.append((item, None))
                continue
            expires_at = as_utc_required(lease.expires_at)
            if now >= expires_at:
                result.append((item, None))
            elif lease.agent_id == agent_id:
                result.append((item, lease.id))
        return result
