"""心跳上报值对象（FR-002，字段见 ADR-0047 §3.3）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.edge_agents.errors import DomainValidationError

AGENT_VERSION_MAX_LENGTH = 64
OS_MAX_LENGTH = 64
ARCH_MAX_LENGTH = 32
LAST_ERROR_MAX_LENGTH = 1000
ADAPTER_VERSIONS_MAX_ITEMS = 32
ADAPTER_NAME_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class HeartbeatReport:
    """Agent 自报的运行状态；服务端以**接收时间**判定存活，不信任此处的时间戳。"""

    reported_at: datetime
    agent_version: str
    os: str
    arch: str
    network_reachable: bool
    disk_free_bytes: int
    spool_pending_count: int
    spool_pending_bytes: int
    upload_queue_depth: int
    adapter_versions: Mapping[str, str] = field(default_factory=dict)
    current_session_id: UUID | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("version", self.agent_version, AGENT_VERSION_MAX_LENGTH),
            ("os", self.os, OS_MAX_LENGTH),
            ("arch", self.arch, ARCH_MAX_LENGTH),
        ):
            if not value.strip() or len(value) > limit:
                raise DomainValidationError(f"{name} 不能为空且不能超过 {limit} 个字符")
        for name, number in (
            ("disk_free_bytes", self.disk_free_bytes),
            ("spool_pending_count", self.spool_pending_count),
            ("spool_pending_bytes", self.spool_pending_bytes),
            ("upload_queue_depth", self.upload_queue_depth),
        ):
            if number < 0:
                raise DomainValidationError(f"{name} 不能为负数")
        if len(self.adapter_versions) > ADAPTER_VERSIONS_MAX_ITEMS:
            raise DomainValidationError(f"adapter_versions 最多 {ADAPTER_VERSIONS_MAX_ITEMS} 项")
        for key, version in self.adapter_versions.items():
            if (
                not key
                or len(key) > ADAPTER_NAME_MAX_LENGTH
                or len(version) > AGENT_VERSION_MAX_LENGTH
            ):
                raise DomainValidationError("adapter_versions 的键或版本长度不合法")
        if self.last_error is not None and len(self.last_error) > LAST_ERROR_MAX_LENGTH:
            raise DomainValidationError(f"last_error 不能超过 {LAST_ERROR_MAX_LENGTH} 个字符")
        if self.reported_at.tzinfo is None or self.reported_at.utcoffset() is None:
            raise DomainValidationError("timestamp 必须带时区")
