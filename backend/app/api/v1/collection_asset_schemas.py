"""采集素材 API DTO（COL-002 + N8）。

请求模型 ``extra="forbid"``，客户端不能提交 status / project_id / version /
审计字段。创建时状态由服务端固定为 ``DRAFT``；之后只能经
``POST .../actions/{activate|archive}`` 迁移，不能直接写 ``status``。

N8 新增三类请求体：

- ``*Patch``：只允许描述性字段（``name`` / ``description``，场景另加
  ``scene_labels``）。``code`` 与执行内容（``steps`` / ``actions``）刻意不出现——
  出现就意味着客户端可以尝试提交，而领域层会拒绝，不如在入口就不收。
- ``AssetActionRequest``：状态动作的可选 ``reason``（全部素材动作共用）。
- 删除无请求体。

响应形状在此新增一个字段 ``allowed_actions``：写路径让状态真正可迁移之后，
「当前状态下客户端能点哪些按钮」必须由服务端说，否则前端要么把四个按钮全渲染
（点了才 409），要么自己实现一遍状态机——后者必然与领域规则漂移。该字段由
``domain.collection.asset_state_machines.allowed_asset_actions`` 计算，
与状态迁移校验同源，不另写一份映射。

``1.8.0`` 时这里没有该字段，因为当时状态恒为 ``DRAFT``、能做什么是常量。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.collection.asset_state_machines import allowed_asset_actions
from app.domain.collection.assets import (
    DESCRIPTION_MAX_LENGTH,
    SCENE_LABEL_MAX_LENGTH,
    SCENE_LABELS_MAX,
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)
from app.domain.collection.validation import REASON_MAX_LENGTH


class CollectionPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    plan_version: int = Field(default=1, ge=1)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    owner_id: UUID | None = None


class CollectionPlanOut(BaseModel):
    plan_id: UUID
    project_id: UUID
    code: str
    name: str
    plan_version: int
    status: str
    description: str | None
    owner_id: UUID | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, plan: CollectionPlan) -> CollectionPlanOut:
        return cls(
            plan_id=plan.plan_id,
            project_id=plan.project_id,
            code=plan.code,
            name=plan.name,
            plan_version=plan.plan_version,
            status=_value(plan.status),
            description=plan.description,
            owner_id=plan.owner_id,
            version=plan.version,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            created_by=plan.created_by,
            updated_by=plan.updated_by,
            allowed_actions=sorted(allowed_asset_actions(plan.status)),
        )


class CollectionSopCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    sop_version: str = Field(min_length=1, max_length=64)
    steps: list[dict[str, object]] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class CollectionSopOut(BaseModel):
    sop_id: UUID
    project_id: UUID
    code: str
    name: str
    sop_version: str
    steps: list[dict[str, object]]
    status: str
    description: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, sop: CollectionSop) -> CollectionSopOut:
        return cls(
            sop_id=sop.sop_id,
            project_id=sop.project_id,
            code=sop.code,
            name=sop.name,
            sop_version=sop.sop_version,
            steps=[dict(item) for item in sop.steps],
            status=_value(sop.status),
            description=sop.description,
            version=sop.version,
            created_at=sop.created_at,
            updated_at=sop.updated_at,
            created_by=sop.created_by,
            updated_by=sop.updated_by,
            allowed_actions=sorted(allowed_asset_actions(sop.status)),
        )


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    scene_labels: list[str] = Field(
        default_factory=list,
        max_length=SCENE_LABELS_MAX,
    )
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class ScenarioOut(BaseModel):
    scenario_id: UUID
    project_id: UUID
    code: str
    name: str
    scene_labels: list[str]
    status: str
    description: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, scenario: Scenario) -> ScenarioOut:
        return cls(
            scenario_id=scenario.scenario_id,
            project_id=scenario.project_id,
            code=scenario.code,
            name=scenario.name,
            scene_labels=list(scenario.scene_labels),
            status=_value(scenario.status),
            description=scenario.description,
            version=scenario.version,
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
            created_by=scenario.created_by,
            updated_by=scenario.updated_by,
            allowed_actions=sorted(allowed_asset_actions(scenario.status)),
        )


class ActionSpecCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    spec_version: str = Field(min_length=1, max_length=64)
    actions: list[dict[str, object]] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class ActionSpecOut(BaseModel):
    action_spec_id: UUID
    project_id: UUID
    code: str
    name: str
    spec_version: str
    actions: list[dict[str, object]]
    status: str
    description: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    allowed_actions: list[str]

    @classmethod
    def from_domain(cls, spec: ActionSpec) -> ActionSpecOut:
        return cls(
            action_spec_id=spec.action_spec_id,
            project_id=spec.project_id,
            code=spec.code,
            name=spec.name,
            spec_version=spec.spec_version,
            actions=[dict(item) for item in spec.actions],
            status=_value(spec.status),
            description=spec.description,
            version=spec.version,
            created_at=spec.created_at,
            updated_at=spec.updated_at,
            created_by=spec.created_by,
            updated_by=spec.updated_by,
            allowed_actions=sorted(allowed_asset_actions(spec.status)),
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


# --- N8：写路径请求体 ------------------------------------------------------
# 全部字段可选：PATCH 语义是"只改提交的字段"，因此用 ``exclude_unset`` 取出
# 真正提交的键。``None`` 是合法值（清空 ``description``），不能与"未提交"混淆。
class CollectionPlanPatch(BaseModel):
    """``code`` / ``plan_version`` 不可改：前者是业务标识，后者标识内容版本。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class CollectionSopPatch(BaseModel):
    """``steps`` 不可改：执行内容变更必须新增版本行（见领域模块文档）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class ScenarioPatch(BaseModel):
    """场景不版本化，故 ``scene_labels`` 可改。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    scene_labels: list[str] | None = Field(
        default=None,
        max_length=SCENE_LABELS_MAX,
    )


class ActionSpecPatch(BaseModel):
    """``actions`` 不可改：执行内容变更必须新增版本行（见领域模块文档）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)


class AssetActionRequest(BaseModel):
    """状态动作请求体；``reason`` 可选（素材动作不强制填原因）。"""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH)


__all__ = [
    "SCENE_LABEL_MAX_LENGTH",
    "ActionSpecCreate",
    "ActionSpecOut",
    "ActionSpecPatch",
    "AssetActionRequest",
    "CollectionPlanCreate",
    "CollectionPlanOut",
    "CollectionPlanPatch",
    "CollectionSopCreate",
    "CollectionSopOut",
    "CollectionSopPatch",
    "ScenarioCreate",
    "ScenarioOut",
    "ScenarioPatch",
]
