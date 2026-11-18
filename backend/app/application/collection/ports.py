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

from app.application.audit import OutboxEvent
from app.application.idempotency import (
    IdempotencyRecord as _IdempotencyRecord,
)
from app.application.idempotency import (
    IdempotencyRepository as _IdempotencyRepository,
)
from app.domain.collection.assets import ActionSpec, CollectionPlan, CollectionSop, Scenario
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

    def list_pending_status(
        self,
        status: CollectionTaskStatus,
        *,
        after_id: UUID | None = None,
        limit: int = 100,
    ) -> list[tuple[UUID, UUID]]:
        """跨项目列出某状态任务的 ``(task_id, project_id)``，供系统任务扫描。"""
        ...


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


class AuditRepository(Protocol):
    def record(self, scope: ProjectScope, record: AuditRecord) -> None: ...


class OutboxRepository(Protocol):
    def enqueue(self, scope: ProjectScope, event: OutboxEvent) -> None: ...


class CollectionPlanRepository(Protocol):
    """采集方案仓储；按 ID 查询必须带项目过滤，跨项目一律返回 ``None``。

    ``save_update`` / ``delete_draft`` 是 N8 新增的写路径（U 与 D）。
    ``save_update`` 接收**已带新内容的实体**与 ``expected_version``，
    由仓储用 ``WHERE version = expected_version`` 做乐观锁；
    未命中时区分 404（行不存在）与 409（版本冲突）。
    """

    def add(self, scope: ProjectScope, plan: CollectionPlan) -> CollectionPlan: ...

    def get(self, scope: ProjectScope, plan_id: UUID) -> CollectionPlan | None: ...

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        plan_version: int,
    ) -> CollectionPlan | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionPlan]: ...

    def save_update(
        self,
        scope: ProjectScope,
        plan: CollectionPlan,
        *,
        expected_version: int,
    ) -> CollectionPlan: ...

    def delete_draft(self, scope: ProjectScope, plan_id: UUID) -> None: ...


class CollectionSopRepository(Protocol):
    def add(self, scope: ProjectScope, sop: CollectionSop) -> CollectionSop: ...

    def get(self, scope: ProjectScope, sop_id: UUID) -> CollectionSop | None: ...

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        sop_version: str,
    ) -> CollectionSop | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionSop]: ...

    def save_update(
        self,
        scope: ProjectScope,
        sop: CollectionSop,
        *,
        expected_version: int,
    ) -> CollectionSop: ...

    def delete_draft(self, scope: ProjectScope, sop_id: UUID) -> None: ...


class ScenarioRepository(Protocol):
    def add(self, scope: ProjectScope, scenario: Scenario) -> Scenario: ...

    def get(self, scope: ProjectScope, scenario_id: UUID) -> Scenario | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Scenario | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Scenario]: ...

    def save_update(
        self,
        scope: ProjectScope,
        scenario: Scenario,
        *,
        expected_version: int,
    ) -> Scenario: ...

    def delete_draft(self, scope: ProjectScope, scenario_id: UUID) -> None: ...


class ActionSpecRepository(Protocol):
    def add(self, scope: ProjectScope, spec: ActionSpec) -> ActionSpec: ...

    def get(self, scope: ProjectScope, spec_id: UUID) -> ActionSpec | None: ...

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        spec_version: str,
    ) -> ActionSpec | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ActionSpec]: ...

    def save_update(
        self,
        scope: ProjectScope,
        spec: ActionSpec,
        *,
        expected_version: int,
    ) -> ActionSpec: ...

    def delete_draft(self, scope: ProjectScope, spec_id: UUID) -> None: ...


class CollectionAssetDirectory(Protocol):
    """任务引用素材时的同项目存在性校验。

    只返回"该 ID 在当前项目里是否存在"，不承载业务规则；理由与
    :class:`AssignmentDirectory` 一致——跨对象前置条件放应用层，
    数据库复合外键兜底。返回值刻意只暴露 ``bool``，避免调用方顺手把
    素材内容读进任务用例、从而耦合两个聚合。
    """

    def plan_exists(self, scope: ProjectScope, plan_id: UUID) -> bool: ...

    def sop_exists(self, scope: ProjectScope, sop_id: UUID) -> bool: ...

    def sop_version_exists(
        self,
        scope: ProjectScope,
        *,
        sop_id: UUID,
        sop_version: str,
    ) -> bool: ...

    def scenario_exists(self, scope: ProjectScope, scenario_id: UUID) -> bool: ...

    def action_spec_exists(self, scope: ProjectScope, spec_id: UUID) -> bool: ...

    def action_spec_version_exists(
        self,
        scope: ProjectScope,
        *,
        spec_id: UUID,
        spec_version: str,
    ) -> bool: ...


# 幂等记录类型与仓储协议由 application 顶层统一声明，避免四个域各写一份。
# 这里重新导出，保持本域既有的 ``from app.application.<domain>.ports import
# IdempotencyRecord`` 调用点不变。
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository
