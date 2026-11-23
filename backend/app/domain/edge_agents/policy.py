"""Agent 时间策略值对象：心跳周期、失联阈值、Lease TTL 等。

数值全部来自配置（``Settings.edge_agent_*``），领域层只校验它们之间的关系，
不写死任何默认值。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.edge_agents.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class EdgeAgentPolicy:
    heartbeat_interval_seconds: int
    offline_after_seconds: int
    lease_ttl_seconds: int
    capture_grace_seconds: int
    pairing_ttl_default_seconds: int
    pairing_ttl_max_seconds: int
    heartbeat_retention_hours: int

    def __post_init__(self) -> None:
        values = (
            self.heartbeat_interval_seconds,
            self.offline_after_seconds,
            self.lease_ttl_seconds,
            self.capture_grace_seconds,
            self.pairing_ttl_default_seconds,
            self.pairing_ttl_max_seconds,
            self.heartbeat_retention_hours,
        )
        if any(value < 1 for value in values):
            raise DomainValidationError("Agent 时间策略的每一项都必须为正整数")
        if self.offline_after_seconds <= self.heartbeat_interval_seconds:
            raise DomainValidationError("失联阈值必须大于心跳周期，否则每次心跳间隙都会被判离线")
        if self.pairing_ttl_default_seconds > self.pairing_ttl_max_seconds:
            raise DomainValidationError("配对凭证默认 TTL 不得超过上限")
