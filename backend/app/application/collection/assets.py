"""采集素材用例：方案 / SOP / 场景 / 动作库的 CRUD 与状态动作（COL-002 + N8）。

为什么用泛型基类而不是四份平行实现
----------------------------------
四类素材的用例流程完全同构：幂等预检 → 构造领域实体（状态固定 ``DRAFT``）
→ 落库 → 同事务写审计与 outbox → 记幂等 → 提交。逐份复制会让"审计字段漏一个"
"幂等忘了记"这类缺陷出现四个变体，也违反"规则只允许存在一处权威实现"。
差异只有三处，用类属性表达：仓储、ID 字段名、审计/事件标签。

读路径（create/list/get）在本模块；写路径（PATCH / 状态动作 / DELETE）在
``_asset_mutations.py`` 的 ``AssetMutationMixin`` 里，避免本文件越过 500 行阈值。
写路径带 ``If-Match`` 乐观锁与幂等键，与任务/批次一致——N8 之前素材没有写路径，
所以那时"不带 ``If-Match``"是缺口的直接后果，不是设计选择。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Generic, Protocol, TypeVar
from uuid import UUID, uuid4

from app.application.collection._asset_mutations import AssetMutationMixin
from app.application.collection.audit import record_change
from app.application.collection.commands import (
    NewActionSpec,
    NewCollectionPlan,
    NewCollectionSop,
    NewScenario,
)
from app.application.collection.idempotency import IdempotencyGuard, validate_key
from app.application.collection.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.collection.ports import (
    ActionSpecRepository,
    AuditRepository,
    CollectionPlanRepository,
    CollectionSopRepository,
    IdempotencyRepository,
    OutboxRepository,
    ScenarioRepository,
    UnitOfWork,
)
from app.domain.collection.asset_lifecycle import (
    ACTION_SPEC_PATCHABLE_FIELDS,
    PLAN_PATCHABLE_FIELDS,
    SCENARIO_PATCHABLE_FIELDS,
    SOP_PATCHABLE_FIELDS,
)
from app.domain.collection.assets import (
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.enums import CollectionAssetStatus
from app.domain.collection.errors import DomainValidationError, ResourceNotFoundError
from app.domain.identity.models import ProjectScope

PLAN_RESOURCE_TYPE = "COLLECTION_PLAN"
SOP_RESOURCE_TYPE = "COLLECTION_SOP"
SCENARIO_RESOURCE_TYPE = "SCENARIO"
ACTION_SPEC_RESOURCE_TYPE = "ACTION_SPEC"

# 事件名必须是字面量：``scripts/check_outbox_events.py`` 用 AST 提取事件类型，
# f-string 会被判失败（R-13）。四者都是**留痕类**（dispatch=False），无消费者。
PLAN_CREATED_EVENT = "collection.plan.created"
SOP_CREATED_EVENT = "collection.sop.created"
SCENARIO_CREATED_EVENT = "collection.scenario.created"
ACTION_SPEC_CREATED_EVENT = "collection.action_spec.created"

# N8 写路径事件：更新 / 状态迁移 / 删除，各三类素材共 9 个。
# 同样必须是字面量（见上），全部为留痕类（dispatch=False），无消费者。
PLAN_UPDATED_EVENT = "collection.plan.updated"
PLAN_STATUS_CHANGED_EVENT = "collection.plan.status_changed"
PLAN_DELETED_EVENT = "collection.plan.deleted"
SOP_UPDATED_EVENT = "collection.sop.updated"
SOP_STATUS_CHANGED_EVENT = "collection.sop.status_changed"
SOP_DELETED_EVENT = "collection.sop.deleted"
SCENARIO_UPDATED_EVENT = "collection.scenario.updated"
SCENARIO_STATUS_CHANGED_EVENT = "collection.scenario.status_changed"
SCENARIO_DELETED_EVENT = "collection.scenario.deleted"
ACTION_SPEC_UPDATED_EVENT = "collection.action_spec.updated"
ACTION_SPEC_STATUS_CHANGED_EVENT = "collection.action_spec.status_changed"
ACTION_SPEC_DELETED_EVENT = "collection.action_spec.deleted"


class _Asset(Protocol):
    """四类素材实体共有的最小形状；只用于类型约束，不承载规则。"""

    code: str
    status: CollectionAssetStatus


class _ListableRepository(Protocol):
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[object]: ...


AssetT = TypeVar("AssetT", bound=_Asset)
RepoT = TypeVar("RepoT", bound=_ListableRepository)


@dataclass(frozen=True, slots=True)
class AssetSpec(Generic[AssetT]):
    """一类素材的元数据：ID 字段名、审计/事件标签、可改字段、用户可见资源名。

    ``patchable`` 来自领域层（``asset_lifecycle``），不在这里另立一份白名单——
    "哪些字段可改"是业务规则，必须在领域层唯一。
    """

    id_field: str
    resource_name: str
    resource_type: str
    created_event: str
    updated_event: str
    status_event: str
    deleted_event: str
    patchable: frozenset[str]


class _AssetUseCases(AssetMutationMixin[AssetT], Generic[AssetT, RepoT]):
    """素材 CRUD 的公共实现；子类只提供 ``spec`` / 仓储与实体工厂。"""

    spec: AssetSpec[AssetT]

    def __init__(
        self,
        repository: RepoT,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # --- 子类实现 -------------------------------------------------------
    def _build(self, scope: ProjectScope, command: object, now: datetime) -> AssetT:
        raise NotImplementedError

    def _event_details(self, asset: AssetT) -> dict[str, object]:
        return {"code": asset.code}

    # --- 公共 CRUD ------------------------------------------------------
    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[AssetT]:
        bounded = normalize_limit(limit)
        items: list[AssetT] = self._repository.list(
            scope,
            status=_asset_status(status),
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=self._id_of)

    def get(self, scope: ProjectScope, asset_id: UUID) -> AssetT:
        asset = self._repository.get(scope, asset_id)  # type: ignore[attr-defined]
        if asset is None:
            raise ResourceNotFoundError(self.spec.resource_name, asset_id)
        return asset  # type: ignore[no-any-return]

    def create(
        self,
        scope: ProjectScope,
        command: object,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AssetT:
        key = validate_key(idempotency_key)
        body = asdict(command)  # type: ignore[call-overload]
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get(scope, _decoded(replay.response_ref, self.spec.resource_name))

        asset = self._build(scope, command, now)
        created = self._repository.add(scope, asset)  # type: ignore[attr-defined]
        asset_id = self._id_of(created)
        details = self._event_details(created)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=self.spec.resource_type,
            resource_id=asset_id,
            action="create",
            from_status=None,
            to_status=_status_value(created.status),
            reason=None,
            request_id=request_id,
            details=details,
            event_type=self.spec.created_event,
            event_payload={self.spec.id_field: str(asset_id), **details},
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(asset_id),
            now=now,
        )
        self._uow.commit()
        return created  # type: ignore[no-any-return]

    def _id_of(self, asset: AssetT) -> UUID:
        value = getattr(asset, self.spec.id_field)
        return value if isinstance(value, UUID) else UUID(str(value))


class CollectionPlanUseCases(_AssetUseCases[CollectionPlan, CollectionPlanRepository]):
    spec = AssetSpec[CollectionPlan](
        id_field="plan_id",
        resource_name="采集方案",
        resource_type=PLAN_RESOURCE_TYPE,
        created_event=PLAN_CREATED_EVENT,
        updated_event=PLAN_UPDATED_EVENT,
        status_event=PLAN_STATUS_CHANGED_EVENT,
        deleted_event=PLAN_DELETED_EVENT,
        patchable=PLAN_PATCHABLE_FIELDS,
    )

    def _build(self, scope: ProjectScope, command: object, now: datetime) -> CollectionPlan:
        if not isinstance(command, NewCollectionPlan):
            raise DomainValidationError("采集方案创建命令类型不符")
        return CollectionPlan(
            plan_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            plan_version=command.plan_version,
            status=CollectionAssetStatus.DRAFT,
            description=command.description,
            owner_id=command.owner_id,
            target_episode_count=command.target_episode_count,
            quality_thresholds=command.quality_thresholds,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )

    def _event_details(self, asset: CollectionPlan) -> dict[str, object]:
        return {"code": asset.code, "plan_version": asset.plan_version}


class CollectionSopUseCases(_AssetUseCases[CollectionSop, CollectionSopRepository]):
    spec = AssetSpec[CollectionSop](
        id_field="sop_id",
        resource_name="SOP",
        resource_type=SOP_RESOURCE_TYPE,
        created_event=SOP_CREATED_EVENT,
        updated_event=SOP_UPDATED_EVENT,
        status_event=SOP_STATUS_CHANGED_EVENT,
        deleted_event=SOP_DELETED_EVENT,
        patchable=SOP_PATCHABLE_FIELDS,
    )

    def _build(self, scope: ProjectScope, command: object, now: datetime) -> CollectionSop:
        if not isinstance(command, NewCollectionSop):
            raise DomainValidationError("SOP 创建命令类型不符")
        return CollectionSop(
            sop_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            sop_version=command.sop_version,
            steps=command.steps,
            status=CollectionAssetStatus.DRAFT,
            description=command.description,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )

    def _event_details(self, asset: CollectionSop) -> dict[str, object]:
        return {"code": asset.code, "sop_version": asset.sop_version}


class ScenarioUseCases(_AssetUseCases[Scenario, ScenarioRepository]):
    spec = AssetSpec[Scenario](
        id_field="scenario_id",
        resource_name="采集场景",
        resource_type=SCENARIO_RESOURCE_TYPE,
        created_event=SCENARIO_CREATED_EVENT,
        updated_event=SCENARIO_UPDATED_EVENT,
        status_event=SCENARIO_STATUS_CHANGED_EVENT,
        deleted_event=SCENARIO_DELETED_EVENT,
        patchable=SCENARIO_PATCHABLE_FIELDS,
    )

    def _build(self, scope: ProjectScope, command: object, now: datetime) -> Scenario:
        if not isinstance(command, NewScenario):
            raise DomainValidationError("采集场景创建命令类型不符")
        return Scenario(
            scenario_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            scene_labels=command.scene_labels,
            status=CollectionAssetStatus.DRAFT,
            description=command.description,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )


class ActionSpecUseCases(_AssetUseCases[ActionSpec, ActionSpecRepository]):
    spec = AssetSpec[ActionSpec](
        id_field="action_spec_id",
        resource_name="动作库",
        resource_type=ACTION_SPEC_RESOURCE_TYPE,
        created_event=ACTION_SPEC_CREATED_EVENT,
        updated_event=ACTION_SPEC_UPDATED_EVENT,
        status_event=ACTION_SPEC_STATUS_CHANGED_EVENT,
        deleted_event=ACTION_SPEC_DELETED_EVENT,
        patchable=ACTION_SPEC_PATCHABLE_FIELDS,
    )

    def _build(self, scope: ProjectScope, command: object, now: datetime) -> ActionSpec:
        if not isinstance(command, NewActionSpec):
            raise DomainValidationError("动作库创建命令类型不符")
        return ActionSpec(
            action_spec_id=uuid4(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            spec_version=command.spec_version,
            actions=command.actions,
            status=CollectionAssetStatus.DRAFT,
            description=command.description,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )

    def _event_details(self, asset: ActionSpec) -> dict[str, object]:
        return {"code": asset.code, "spec_version": asset.spec_version}


def _asset_status(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return CollectionAssetStatus(value).value
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _status_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _decoded(response_ref: str | None, resource: str) -> UUID:
    if not response_ref:
        raise ResourceNotFoundError(resource, response_ref)
    try:
        return UUID(response_ref)
    except ValueError as exc:
        raise ResourceNotFoundError(resource, response_ref) from exc


def _utcnow() -> datetime:
    return datetime.now(UTC)


# 素材类别 -> 审计资源类型；API 层统一取用，避免路由里各写一份字符串。
ASSET_RESOURCE_TYPES: Mapping[str, str] = {
    "plans": PLAN_RESOURCE_TYPE,
    "sops": SOP_RESOURCE_TYPE,
    "scenarios": SCENARIO_RESOURCE_TYPE,
    "action-specs": ACTION_SPEC_RESOURCE_TYPE,
}

__all__ = [
    "ACTION_SPEC_CREATED_EVENT",
    "ACTION_SPEC_DELETED_EVENT",
    "ACTION_SPEC_RESOURCE_TYPE",
    "ACTION_SPEC_STATUS_CHANGED_EVENT",
    "ACTION_SPEC_UPDATED_EVENT",
    "ASSET_RESOURCE_TYPES",
    "PLAN_CREATED_EVENT",
    "PLAN_DELETED_EVENT",
    "PLAN_RESOURCE_TYPE",
    "PLAN_STATUS_CHANGED_EVENT",
    "PLAN_UPDATED_EVENT",
    "SCENARIO_CREATED_EVENT",
    "SCENARIO_DELETED_EVENT",
    "SCENARIO_RESOURCE_TYPE",
    "SCENARIO_STATUS_CHANGED_EVENT",
    "SCENARIO_UPDATED_EVENT",
    "SOP_CREATED_EVENT",
    "SOP_DELETED_EVENT",
    "SOP_RESOURCE_TYPE",
    "SOP_STATUS_CHANGED_EVENT",
    "SOP_UPDATED_EVENT",
    "ActionSpecUseCases",
    "AssetSpec",
    "CollectionPlanUseCases",
    "CollectionSopUseCases",
    "ScenarioUseCases",
]
