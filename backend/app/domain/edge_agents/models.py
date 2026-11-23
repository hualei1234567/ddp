"""Agent 身份与一次性配对凭证（FR-001）。

凭据明文**不进入领域对象**：领域层只知道 Agent 与凭证的业务状态；token 的生成、
哈希与比对在 ``app.infrastructure.auth.agent_tokens``，仓储只存哈希。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.edge_agents.enums import (
    AGENT_TRANSITIONS,
    AgentStatus,
    Connectivity,
)
from app.domain.edge_agents.errors import (
    AgentAuthenticationError,
    DomainValidationError,
    StateTransitionError,
)
from app.domain.edge_agents.heartbeat import AGENT_VERSION_MAX_LENGTH
from app.domain.edge_agents.policy import EdgeAgentPolicy

DISPLAY_NAME_MAX_LENGTH = 128
REASON_MAX_LENGTH = 1000
OS_ARCH_MAX_LENGTH = 96
MAX_SCOPE_DEVICES = 64


def _clean_device_ids(device_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
    if len(device_ids) > MAX_SCOPE_DEVICES or len(set(device_ids)) != len(device_ids):
        raise DomainValidationError("device_ids 不得重复，且数量不能超过上限")
    return device_ids


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DomainValidationError("display_name 不能为空")
    if len(cleaned) > DISPLAY_NAME_MAX_LENGTH:
        raise DomainValidationError(f"display_name 不能超过 {DISPLAY_NAME_MAX_LENGTH} 个字符")
    return cleaned


def clean_reason(value: str | None, *, required: bool) -> str | None:
    """原因文本：必填场景下不得为空白；统一限长。"""
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise DomainValidationError("必须提供原因")
        return None
    if len(cleaned) > REASON_MAX_LENGTH:
        raise DomainValidationError(f"原因不能超过 {REASON_MAX_LENGTH} 个字符")
    return cleaned


@dataclass(frozen=True, slots=True)
class PairingToken:
    """管理员签发的一次性配对凭证（短 TTL、单次使用）。"""

    token_id: UUID
    project_id: UUID
    robot_id: UUID
    display_name: str
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    device_ids: tuple[UUID, ...] = ()
    consumed_at: datetime | None = None
    agent_id: UUID | None = None

    @classmethod
    def issue(
        cls,
        *,
        token_id: UUID,
        project_id: UUID,
        robot_id: UUID,
        display_name: str,
        created_by: UUID,
        now: datetime,
        ttl_seconds: int | None,
        policy: EdgeAgentPolicy,
        device_ids: tuple[UUID, ...] = (),
    ) -> PairingToken:
        ttl = policy.pairing_ttl_default_seconds if ttl_seconds is None else ttl_seconds
        if ttl < 1 or ttl > policy.pairing_ttl_max_seconds:
            raise DomainValidationError(
                f"配对凭证 TTL 必须在 1 到 {policy.pairing_ttl_max_seconds} 秒之间"
            )
        return cls(
            token_id=token_id,
            project_id=project_id,
            robot_id=robot_id,
            display_name=_clean_name(display_name),
            created_by=created_by,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            device_ids=_clean_device_ids(device_ids),
        )

    def ensure_redeemable(self, now: datetime) -> None:
        """过期、已使用一律同一个错误：不向未认证调用方泄露凭证状态。"""
        if self.consumed_at is not None or now >= self.expires_at:
            raise AgentAuthenticationError("配对凭证无效或已过期")


@dataclass(frozen=True, slots=True)
class EdgeAgent:
    """Edge Capture Agent 的服务端身份记录。"""

    agent_id: UUID
    project_id: UUID
    display_name: str
    robot_id: UUID
    status: AgentStatus
    created_by: UUID
    created_at: datetime
    paired_at: datetime
    device_ids: tuple[UUID, ...] = ()
    dispatch_paused: bool = False
    activated_at: datetime | None = None
    last_seen_at: datetime | None = None
    agent_version: str | None = None
    os_arch: str | None = None
    token_generation: int = 1
    token_rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None
    revoke_reason: str | None = None

    @classmethod
    def from_pairing(
        cls,
        token: PairingToken,
        *,
        agent_id: UUID,
        now: datetime,
        agent_version: str | None = None,
        os_arch: str | None = None,
    ) -> EdgeAgent:
        token.ensure_redeemable(now)
        if agent_version is not None and len(agent_version) > AGENT_VERSION_MAX_LENGTH:
            raise DomainValidationError("version 过长")
        if os_arch is not None and len(os_arch) > OS_ARCH_MAX_LENGTH:
            raise DomainValidationError("os_arch 过长")
        return cls(
            agent_id=agent_id,
            project_id=token.project_id,
            display_name=token.display_name,
            robot_id=token.robot_id,
            status=AgentStatus.PAIRED,
            created_by=token.created_by,
            created_at=now,
            paired_at=now,
            device_ids=token.device_ids,
            agent_version=agent_version,
            os_arch=os_arch,
        )

    @property
    def is_revoked(self) -> bool:
        return self.status is AgentStatus.REVOKED

    def ensure_transition(self, target: AgentStatus) -> None:
        if target not in AGENT_TRANSITIONS[self.status]:
            raise StateTransitionError(f"Agent 不允许从 {self.status.value} 迁移到 {target.value}")

    def record_heartbeat(
        self,
        now: datetime,
        agent_version: str | None,
        os_arch: str | None = None,
    ) -> EdgeAgent:
        """心跳：首次心跳把 PAIRED 推进到 ACTIVE；已撤销的 Agent 不能再产生心跳。"""
        if self.is_revoked:
            raise StateTransitionError("已撤销的 Agent 不能上报心跳")
        if agent_version is not None and len(agent_version) > AGENT_VERSION_MAX_LENGTH:
            raise DomainValidationError("version 过长")
        if os_arch is not None and len(os_arch) > OS_ARCH_MAX_LENGTH:
            raise DomainValidationError("os_arch 过长")
        activated = self.status is AgentStatus.PAIRED
        return replace(
            self,
            status=AgentStatus.ACTIVE,
            activated_at=now if activated else self.activated_at,
            last_seen_at=now,
            agent_version=agent_version,
            os_arch=os_arch if os_arch is not None else self.os_arch,
        )

    def set_dispatch_paused(self, paused: bool) -> EdgeAgent:
        """暂停或恢复新任务派发；已撤销的 Agent 不能变更派发状态。"""
        if self.is_revoked:
            raise StateTransitionError("已撤销的 Agent 不能修改派发状态")
        return replace(self, dispatch_paused=paused)

    def revoke(self, now: datetime, *, actor_id: UUID, reason: str | None) -> EdgeAgent:
        """撤销为终态；在途 Lease 的回收由用例在同一事务内完成。"""
        self.ensure_transition(AgentStatus.REVOKED)
        return replace(
            self,
            status=AgentStatus.REVOKED,
            revoked_at=now,
            revoked_by=actor_id,
            revoke_reason=clean_reason(reason, required=True),
        )

    def rotate_token(self, now: datetime) -> EdgeAgent:
        if self.is_revoked:
            raise StateTransitionError("已撤销的 Agent 不能轮换 token")
        return replace(self, token_generation=self.token_generation + 1, token_rotated_at=now)

    def ensure_can_hold_lease(self) -> None:
        if self.status is not AgentStatus.ACTIVE:
            raise StateTransitionError("Agent 必须先成功上报心跳（ACTIVE）才能申请 Lease")
        if self.dispatch_paused:
            raise StateTransitionError("Agent 已暂停新任务派发，不能申请新 Lease")

    def connectivity(self, now: datetime, policy: EdgeAgentPolicy) -> Connectivity:
        """读取时派生：已撤销、从未心跳、超失联阈值均为 OFFLINE；超心跳间隔为 DELAYED。"""
        if self.is_revoked or self.last_seen_at is None:
            return Connectivity.OFFLINE
        age = (now - self.last_seen_at).total_seconds()
        if age > policy.offline_after_seconds:
            return Connectivity.OFFLINE
        if age > policy.heartbeat_interval_seconds:
            return Connectivity.DELAYED
        return Connectivity.ONLINE
