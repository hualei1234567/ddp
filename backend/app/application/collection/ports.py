"""采集域仓储与外部适配端口。

所有方法都要求传入服务端构造的 :class:`ProjectScope`；按 ID 查询必须带
项目过滤，跨项目资源返回 ``None`` 并由用例统一映射为 404。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.collection.enums import CollectionBatchStatus, CollectionTaskStatus
from app.domain.collection.models import CollectionBatch, CollectionTask
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    def commit(self) -> None: ...


class CollectionTaskRepository(Protocol):
    def add(self, scope: ProjectScope, task: CollectionTask) -> CollectionTask: ...

    def get(self, scope: ProjectScope, task_id: UUID) -> CollectionTask | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> CollectionTask | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: CollectionTaskStatus | None = None,
        assigned_to: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionTask]: ...

    def save(
        self,
        scope: ProjectScope,
        task: CollectionTask,
        *,
        expected_version: int,
    ) -> CollectionTask: ...


class CollectionBatchRepository(Protocol):
    def add(self, scope: ProjectScope, batch: CollectionBatch) -> CollectionBatch: ...

    def get(self, scope: ProjectScope, batch_id: UUID) -> CollectionBatch | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> CollectionBatch | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
        status: CollectionBatchStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionBatch]: ...

    def list_non_terminal(
        self,
        scope: ProjectScope,
        *,
        task_id: UUID,
    ) -> list[CollectionBatch]: ...

    def next_sequence_no(self, scope: ProjectScope, task_id: UUID) -> int: ...

    def sum_expected_count(self, scope: ProjectScope, task_id: UUID) -> int: ...

    def save(
        self,
        scope: ProjectScope,
        batch: CollectionBatch,
        *,
        expected_version: int,
    ) -> CollectionBatch: ...


class AssignmentDirectory(Protocol):
    """校验分配对象是否同项目且可用；只返回状态，不承载业务规则。"""

    def user_is_member(self, scope: ProjectScope, user_id: UUID) -> bool: ...

    def robot_status(self, scope: ProjectScope, robot_id: UUID) -> str | None: ...

    def device_status(self, scope: ProjectScope, device_id: UUID) -> str | None: ...

    def sensor_status(self, scope: ProjectScope, sensor_id: UUID) -> str | None: ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: UUID
    project_id: UUID
    actor_user_id: UUID
    actor_role: str | None
    resource_type: str
    resource_id: UUID
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    request_id: str | None
    result: str
    details: Mapping[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    event_id: UUID
    project_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: Mapping[str, object]
    occurred_at: datetime


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明，避免四个域各写一份。
# 这里重新导出，保持本域既有的 ``from app.application.<domain>.ports import
# IdempotencyRecord`` 调用点不变。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository
