"""采集素材 SQLAlchemy 仓储：方案 / SOP / 场景 / 动作库（COL-002 + N8）。

四张表的读写形状同构，字段映射用模块级小函数复用；不复用基类：
SQLAlchemy 模型类型不同，硬套泛型会让 pytest / mypy 都读不出真实字段，
反而比四段直白代码更难核对。

所有查询强制 ``project_id`` 过滤（``scoped_select``），按 ID 取单条也带项目条件——
跨项目资源返回 ``None``，由用例统一映射为 404，不泄漏"该 ID 在别的项目存在"。

N8 补齐 ``save_update`` / ``delete_draft`` 两个写入口（CRUD 的 U 与 D）。
两者的**规则公共部分**（乐观锁、删除前置判定）在
:mod:`app.infrastructure.repositories._collection_asset_write` 里只有一份实现，
本文件只负责"哪类素材用哪个 Model / id 列 / 可改字段"。四段直白代码比硬套泛型
更容易核对，而真正会写出变体的是规则、不是字段映射，所以按这个粒度拆分。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.collection.assets import (
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.errors import DomainValidationError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.db.models.collection_assets import (
    ActionSpecModel,
    CollectionPlanModel,
    CollectionSopModel,
    ScenarioModel,
)
from app.infrastructure.repositories._collection_asset_write import (
    delete_asset_draft,
    save_asset_update,
)
from app.infrastructure.repositories._collection_sql import (
    as_utc,
    enum_value,
    flush_or_duplicate,
    scoped_select,
)

# 仓储层允许的最大 limit 与既有采集仓储一致（列表用例取 limit+1 判定下一页）。
_FETCH_LIMIT_MAX = 101


def _check_limit(limit: int) -> None:
    if limit < 1 or limit > _FETCH_LIMIT_MAX:
        raise DomainValidationError(f"limit 必须在 1 到 {_FETCH_LIMIT_MAX} 之间")


class SqlCollectionPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, plan: CollectionPlan) -> CollectionPlan:
        scope.ensure(plan.project_id)
        now = datetime.now(UTC)
        self._session.add(
            CollectionPlanModel(
                id=plan.plan_id,
                project_id=plan.project_id,
                code=plan.code,
                name=plan.name,
                plan_version=plan.plan_version,
                status=enum_value(plan.status),
                description=plan.description,
                owner_id=plan.owner_id,
                target_episode_count=plan.target_episode_count,
                quality_thresholds=dict(plan.quality_thresholds),
                version=plan.version,
                created_at=plan.created_at or now,
                updated_at=plan.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return plan

    def get(self, scope: ProjectScope, plan_id: UUID) -> CollectionPlan | None:
        row = self._session.scalar(
            select(CollectionPlanModel).where(
                CollectionPlanModel.id == plan_id,
                CollectionPlanModel.project_id == scope.project_id,
            )
        )
        return None if row is None else _plan(row)

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        plan_version: int,
    ) -> CollectionPlan | None:
        row = self._session.scalar(
            select(CollectionPlanModel).where(
                CollectionPlanModel.project_id == scope.project_id,
                CollectionPlanModel.code == code.strip(),
                CollectionPlanModel.plan_version == plan_version,
            )
        )
        return None if row is None else _plan(row)

    def save_update(
        self,
        scope: ProjectScope,
        plan: CollectionPlan,
        *,
        expected_version: int,
    ) -> CollectionPlan:
        scope.ensure(plan.project_id)
        # ``code`` / ``plan_version`` 不在可改字段里，故不传给仓储——
        # 显式省略比"写了同样的值"更能表达"这两个字段不可变"。
        save_asset_update(
            self._session,
            scope,
            model=CollectionPlanModel,
            asset_id=plan.plan_id,
            values={
                "name": plan.name,
                "description": plan.description,
                "owner_id": plan.owner_id,
                "target_episode_count": plan.target_episode_count,
                "quality_thresholds": dict(plan.quality_thresholds),
                "status": enum_value(plan.status),
            },
            expected_version=expected_version,
            resource_name="采集方案",
        )
        return replace(plan, version=expected_version + 1, updated_by=scope.actor_id)

    def delete_draft(self, scope: ProjectScope, plan_id: UUID) -> None:
        delete_asset_draft(
            self._session,
            scope,
            model=CollectionPlanModel,
            asset_id=plan_id,
            task_column=CollectionTask.plan_id,
            resource_name="采集方案",
        )

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionPlan]:
        _check_limit(limit)
        statement = scoped_select(CollectionPlanModel, scope)
        if status is not None:
            statement = statement.where(CollectionPlanModel.status == status)
        if after_id is not None:
            statement = statement.where(CollectionPlanModel.id > after_id)
        statement = statement.order_by(CollectionPlanModel.id).limit(limit)
        return [_plan(row) for row in self._session.scalars(statement)]


class SqlCollectionSopRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, sop: CollectionSop) -> CollectionSop:
        scope.ensure(sop.project_id)
        now = datetime.now(UTC)
        self._session.add(
            CollectionSopModel(
                id=sop.sop_id,
                project_id=sop.project_id,
                code=sop.code,
                name=sop.name,
                sop_version=sop.sop_version,
                steps=[dict(item) for item in sop.steps],
                status=enum_value(sop.status),
                description=sop.description,
                version=sop.version,
                created_at=sop.created_at or now,
                updated_at=sop.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return sop

    def get(self, scope: ProjectScope, sop_id: UUID) -> CollectionSop | None:
        row = self._session.scalar(
            select(CollectionSopModel).where(
                CollectionSopModel.id == sop_id,
                CollectionSopModel.project_id == scope.project_id,
            )
        )
        return None if row is None else _sop(row)

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        sop_version: str,
    ) -> CollectionSop | None:
        row = self._session.scalar(
            select(CollectionSopModel).where(
                CollectionSopModel.project_id == scope.project_id,
                CollectionSopModel.code == code.strip(),
                CollectionSopModel.sop_version == sop_version.strip(),
            )
        )
        return None if row is None else _sop(row)

    def save_update(
        self,
        scope: ProjectScope,
        sop: CollectionSop,
        *,
        expected_version: int,
    ) -> CollectionSop:
        scope.ensure(sop.project_id)
        # ``code`` / ``sop_version`` / ``steps`` 不在可改字段里：
        # 前两者是业务标识与版本快照锚点，后者是任务执行内容。
        save_asset_update(
            self._session,
            scope,
            model=CollectionSopModel,
            asset_id=sop.sop_id,
            values={
                "name": sop.name,
                "description": sop.description,
                "status": enum_value(sop.status),
            },
            expected_version=expected_version,
            resource_name="SOP",
        )
        return replace(sop, version=expected_version + 1, updated_by=scope.actor_id)

    def delete_draft(self, scope: ProjectScope, sop_id: UUID) -> None:
        delete_asset_draft(
            self._session,
            scope,
            model=CollectionSopModel,
            asset_id=sop_id,
            task_column=CollectionTask.sop_id,
            resource_name="SOP",
        )

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CollectionSop]:
        _check_limit(limit)
        statement = scoped_select(CollectionSopModel, scope)
        if status is not None:
            statement = statement.where(CollectionSopModel.status == status)
        if after_id is not None:
            statement = statement.where(CollectionSopModel.id > after_id)
        statement = statement.order_by(CollectionSopModel.id).limit(limit)
        return [_sop(row) for row in self._session.scalars(statement)]


class SqlScenarioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, scenario: Scenario) -> Scenario:
        scope.ensure(scenario.project_id)
        now = datetime.now(UTC)
        self._session.add(
            ScenarioModel(
                id=scenario.scenario_id,
                project_id=scenario.project_id,
                code=scenario.code,
                name=scenario.name,
                scene_labels=list(scenario.scene_labels),
                status=enum_value(scenario.status),
                description=scenario.description,
                version=scenario.version,
                created_at=scenario.created_at or now,
                updated_at=scenario.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return scenario

    def get(self, scope: ProjectScope, scenario_id: UUID) -> Scenario | None:
        row = self._session.scalar(
            select(ScenarioModel).where(
                ScenarioModel.id == scenario_id,
                ScenarioModel.project_id == scope.project_id,
            )
        )
        return None if row is None else _scenario(row)

    def get_by_code(self, scope: ProjectScope, code: str) -> Scenario | None:
        row = self._session.scalar(
            select(ScenarioModel).where(
                ScenarioModel.project_id == scope.project_id,
                ScenarioModel.code == code.strip(),
            )
        )
        return None if row is None else _scenario(row)

    def save_update(
        self,
        scope: ProjectScope,
        scenario: Scenario,
        *,
        expected_version: int,
    ) -> Scenario:
        scope.ensure(scenario.project_id)
        # ``code`` 不可改（业务标识）；``scene_labels`` 可改——
        # 场景不版本化，标签只参与检索（见 asset_lifecycle 的论证）。
        save_asset_update(
            self._session,
            scope,
            model=ScenarioModel,
            asset_id=scenario.scenario_id,
            values={
                "name": scenario.name,
                "scene_labels": list(scenario.scene_labels),
                "description": scenario.description,
                "status": enum_value(scenario.status),
            },
            expected_version=expected_version,
            resource_name="采集场景",
        )
        return replace(scenario, version=expected_version + 1, updated_by=scope.actor_id)

    def delete_draft(self, scope: ProjectScope, scenario_id: UUID) -> None:
        delete_asset_draft(
            self._session,
            scope,
            model=ScenarioModel,
            asset_id=scenario_id,
            task_column=CollectionTask.scenario_id,
            resource_name="采集场景",
        )

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Scenario]:
        _check_limit(limit)
        statement = scoped_select(ScenarioModel, scope)
        if status is not None:
            statement = statement.where(ScenarioModel.status == status)
        if after_id is not None:
            statement = statement.where(ScenarioModel.id > after_id)
        statement = statement.order_by(ScenarioModel.id).limit(limit)
        return [_scenario(row) for row in self._session.scalars(statement)]


class SqlActionSpecRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, spec: ActionSpec) -> ActionSpec:
        scope.ensure(spec.project_id)
        now = datetime.now(UTC)
        self._session.add(
            ActionSpecModel(
                id=spec.action_spec_id,
                project_id=spec.project_id,
                code=spec.code,
                name=spec.name,
                spec_version=spec.spec_version,
                actions=[dict(item) for item in spec.actions],
                status=enum_value(spec.status),
                description=spec.description,
                version=spec.version,
                created_at=spec.created_at or now,
                updated_at=spec.updated_at or now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
        )
        flush_or_duplicate(self._session)
        return spec

    def get(self, scope: ProjectScope, spec_id: UUID) -> ActionSpec | None:
        row = self._session.scalar(
            select(ActionSpecModel).where(
                ActionSpecModel.id == spec_id,
                ActionSpecModel.project_id == scope.project_id,
            )
        )
        return None if row is None else _action_spec(row)

    def get_by_code_version(
        self,
        scope: ProjectScope,
        *,
        code: str,
        spec_version: str,
    ) -> ActionSpec | None:
        row = self._session.scalar(
            select(ActionSpecModel).where(
                ActionSpecModel.project_id == scope.project_id,
                ActionSpecModel.code == code.strip(),
                ActionSpecModel.spec_version == spec_version.strip(),
            )
        )
        return None if row is None else _action_spec(row)

    def save_update(
        self,
        scope: ProjectScope,
        spec: ActionSpec,
        *,
        expected_version: int,
    ) -> ActionSpec:
        scope.ensure(spec.project_id)
        # ``code`` / ``spec_version`` / ``actions`` 不在可改字段里，
        # 理由与 SOP 的 ``steps`` 相同：执行内容只能新增版本行。
        save_asset_update(
            self._session,
            scope,
            model=ActionSpecModel,
            asset_id=spec.action_spec_id,
            values={
                "name": spec.name,
                "description": spec.description,
                "status": enum_value(spec.status),
            },
            expected_version=expected_version,
            resource_name="动作库",
        )
        return replace(spec, version=expected_version + 1, updated_by=scope.actor_id)

    def delete_draft(self, scope: ProjectScope, spec_id: UUID) -> None:
        delete_asset_draft(
            self._session,
            scope,
            model=ActionSpecModel,
            asset_id=spec_id,
            task_column=CollectionTask.action_spec_id,
            resource_name="动作库",
        )

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[ActionSpec]:
        _check_limit(limit)
        statement = scoped_select(ActionSpecModel, scope)
        if status is not None:
            statement = statement.where(ActionSpecModel.status == status)
        if after_id is not None:
            statement = statement.where(ActionSpecModel.id > after_id)
        statement = statement.order_by(ActionSpecModel.id).limit(limit)
        return [_action_spec(row) for row in self._session.scalars(statement)]


def _plan(row: CollectionPlanModel) -> CollectionPlan:
    return CollectionPlan(
        plan_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        plan_version=row.plan_version,
        status=row.status,
        description=row.description,
        owner_id=row.owner_id,
        target_episode_count=row.target_episode_count,
        quality_thresholds=dict(row.quality_thresholds or {}),
        version=row.version,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _sop(row: CollectionSopModel) -> CollectionSop:
    return CollectionSop(
        sop_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        sop_version=row.sop_version,
        steps=tuple(row.steps or ()),
        status=row.status,
        description=row.description,
        version=row.version,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _scenario(row: ScenarioModel) -> Scenario:
    return Scenario(
        scenario_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        scene_labels=tuple(row.scene_labels or ()),
        status=row.status,
        description=row.description,
        version=row.version,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _action_spec(row: ActionSpecModel) -> ActionSpec:
    return ActionSpec(
        action_spec_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        spec_version=row.spec_version,
        actions=tuple(row.actions or ()),
        status=row.status,
        description=row.description,
        version=row.version,
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


__all__ = [
    "SqlActionSpecRepository",
    "SqlCollectionPlanRepository",
    "SqlCollectionSopRepository",
    "SqlScenarioRepository",
]
