"""Edge Agent 域枚举（V31-011 / ADR-0047）。

``AgentStatus`` 是落库的身份状态；``Connectivity`` 与 ``LeaseStatus`` 是**读取时
派生**的展示值，不落库——失联只是展示与派发抑制，不改变身份（ADR-0047 §3.3）。
"""

from __future__ import annotations

from enum import StrEnum


class AgentStatus(StrEnum):
    """Agent 身份状态：``REVOKED`` 为终态，不可回退。"""

    PAIRED = "PAIRED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class Connectivity(StrEnum):
    """由 ``last_seen_at`` 与心跳/失联阈值在读取时派生。"""

    ONLINE = "ONLINE"
    DELAYED = "DELAYED"
    OFFLINE = "OFFLINE"


class LeaseStatus(StrEnum):
    """Lease 的派生状态；``EXPIRED`` 表示未释放但已过期（等待被回收）。"""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class LeaseReleaseReason(StrEnum):
    """Lease 释放原因（必记，审计依据）。

    前三项由 Agent 主动声明；后三项只能由服务端写入。
    """

    COMPLETED = "COMPLETED"
    RELEASED_BY_AGENT = "RELEASED_BY_AGENT"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    AGENT_REVOKED = "AGENT_REVOKED"
    ADMIN_RECLAIMED = "ADMIN_RECLAIMED"


#: Agent 可以自报的释放原因；服务端原因不得由客户端指定。
AGENT_RELEASE_REASONS: frozenset[LeaseReleaseReason] = frozenset(
    {
        LeaseReleaseReason.COMPLETED,
        LeaseReleaseReason.RELEASED_BY_AGENT,
        LeaseReleaseReason.FAILED,
    }
)

#: 合法的 Agent 状态迁移（``REVOKED`` 没有出边）。
AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.PAIRED: frozenset({AgentStatus.ACTIVE, AgentStatus.REVOKED}),
    AgentStatus.ACTIVE: frozenset({AgentStatus.REVOKED}),
    AgentStatus.REVOKED: frozenset(),
}


def agent_transition_pairs() -> frozenset[tuple[str, str]]:
    """供数据库触发器与测试复用的 (from, to) 白名单。"""
    return frozenset(
        (current.value, target.value)
        for current, targets in AGENT_TRANSITIONS.items()
        for target in targets
    )
