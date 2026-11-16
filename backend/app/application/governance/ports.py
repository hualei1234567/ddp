"""治理域应用层端口。

跨模块只通过本文件声明的 Protocol 与 DTO 交互；SQL 细节在
``app.infrastructure.repositories.governance``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.audit import (
    AuditRepository as _AuditRepository,
)
from app.application.audit import (
    OutboxRepository as _OutboxRepository,
)
from app.domain.governance.enums import ResourceKind
from app.domain.governance.models import (
    ResourceQuota,
    ResourceUsage,
    UsageSnapshot,
)
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class QuotaRepository(Protocol):
    def get(self, scope: ProjectScope, kind: ResourceKind) -> ResourceQuota | None: ...

    def list(self, scope: ProjectScope) -> list[ResourceQuota]: ...

    def create(self, scope: ProjectScope, quota: ResourceQuota) -> ResourceQuota: ...

    def save(
        self,
        scope: ProjectScope,
        quota: ResourceQuota,
        *,
        expected_version: int,
    ) -> ResourceQuota: ...


class UsageReader(Protocol):
    """权威用量读取。

    ``measure`` 只做**项目内、按资源类型**的聚合，供写操作配额判定使用；
    ``measure_all`` 供离线 Worker 生成快照。两者都不得返回未经来源标注的值。
    """

    def measure(
        self,
        scope: ProjectScope,
        kind: ResourceKind,
        *,
        at: datetime,
    ) -> ResourceUsage: ...

    def measure_all(
        self,
        *,
        at: datetime,
    ) -> Mapping[UUID, Mapping[ResourceKind, ResourceUsage]]: ...


class UsageSnapshotRepository(Protocol):
    def latest(self, scope: ProjectScope) -> Mapping[ResourceKind, ResourceUsage]: ...

    def replace_all(self, rows: Sequence[UsageSnapshot]) -> None: ...


class ReplayDetector(Protocol):
    """判断本次请求是否是已成功写动作的幂等重放。

    配额判定必须在**重放**时让路：重放不会新建资源、不消耗配额，
    若因"当前已到上限"而拒绝，客户端就再也拿不回原本成功的结果。
    """

    def is_replay(
        self,
        scope: ProjectScope,
        key: str,
        *,
        method: str,
        path: str,
        now: datetime,
    ) -> bool: ...


# 审计与 outbox 的 DTO/协议由 application 顶层统一声明；本域直接复用。
AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository

__all__ = [
    "AuditRepository",
    "OutboxRepository",
    "QuotaRepository",
    "ReplayDetector",
    "UnitOfWork",
    "UsageReader",
    "UsageSnapshotRepository",
]
