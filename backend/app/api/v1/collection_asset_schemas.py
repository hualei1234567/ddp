"""采集素材 API DTO（COL-002）。

请求模型 ``extra="forbid"``，客户端不能提交 status / project_id / version /
审计字段。状态由服务端固定为 ``DRAFT``（与任务创建一致）。

响应字段是本轮**新增端点**的对外形状；契约文件
``packages/contracts/openapi.yaml`` 尚未声明这四个路径（按任务要求不改契约），
缺口已写入交付报告，待 System Architect 走契约变更流程。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.collection.assets import (
    DESCRIPTION_MAX_LENGTH,
    SCENE_LABEL_MAX_LENGTH,
    SCENE_LABELS_MAX,
    ActionSpec,
    CollectionPlan,
    CollectionSop,
    Scenario,
)


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
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "SCENE_LABEL_MAX_LENGTH",
    "ActionSpecCreate",
    "ActionSpecOut",
    "CollectionPlanCreate",
    "CollectionPlanOut",
    "CollectionSopCreate",
    "CollectionSopOut",
    "ScenarioCreate",
    "ScenarioOut",
]
