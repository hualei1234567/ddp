"""场地域仓储与目录端口。

所有方法都要求传入服务端构造的 :class:`ProjectScope`；按 ID 查询必须带项目
过滤，跨项目资源返回 ``None`` 并由用例统一映射为 404（不泄漏"该 ID 在别的
项目存在"）。

:class:`LocationDirectory` 与采集域的 :class:`CollectionAssetDirectory` 同一
职责定位：只回答"该场地在当前项目里存不存在"，供设备域与采集域在写入前给出
**可读错误**。返回值刻意只暴露 ``bool``，避免调用方顺手把场地内容读进设备/
任务用例、从而耦合两个聚合。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from app.application.audit import (
    AuditRepository as _AuditRepository,
)
from app.application.audit import (
    OutboxRepository as _OutboxRepository,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.identity.models import ProjectScope
from app.domain.locations.models import Location, LocationResourceCounts


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class LocationRepository(Protocol):
    def add(self, scope: ProjectScope, location: Location) -> Location: ...

    def get(self, scope: ProjectScope, location_id: UUID) -> Location | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Location | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        kind: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Location]: ...

    def save(
        self,
        scope: ProjectScope,
        location: Location,
        *,
        expected_version: int,
    ) -> Location: ...


class SiteResourceSummaryRepository(Protocol):
    """按场地聚合资源计数的只读端口（N9-005）。

    只返回计数，不返回设备/传感器/任务实体：概览视图没有读取这些聚合内部
    字段的授权，多暴露一列就等于给该端点偷偷扩权。聚合全部下推到 SQL，
    应用层拿到的只是 ``location_id -> 计数`` 的映射。
    """

    def count_by_location(
        self,
        scope: ProjectScope,
        *,
        location_ids: Sequence[UUID],
    ) -> Mapping[UUID, LocationResourceCounts]: ...


class LocationDirectory(Protocol):
    """场地存在性校验（同项目）；供设备域与采集域写入前调用。"""

    def location_exists(self, scope: ProjectScope, location_id: UUID) -> bool: ...


AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository
IdempotencyRepository = _IdempotencyRepository

__all__ = [
    "AuditRepository",
    "IdempotencyRepository",
    "LocationDirectory",
    "LocationRepository",
    "OutboxRepository",
    "SiteResourceSummaryRepository",
    "UnitOfWork",
]
