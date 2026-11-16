"""采集素材 HTTP 路由：采集方案 / SOP / 场景 / 动作库（COL-002 + N8）。

路由只做协议适配（路径、请求体、If-Match、Idempotency-Key），业务规则在
``app/application/collection/assets.py`` 与 ``_asset_mutations.py``；
项目隔离与权限由服务端依赖强制。

本文件只保留读路径与创建（COL-002）。N8 的写路径（PATCH / 状态动作 / DELETE）
在 ``collection_asset_write_routes.py``：12 个写端点全部塞进来会让本文件越过
500 行评审阈值，而写路径又共享同一组协议约束（``If-Match`` /
``Idempotency-Key`` / ``DeleteScope``），按"读+创建 / 写"切分最自然。

两个模块共用同一个 ``router`` 实例，因此路径前缀与 OpenAPI tag 不会分叉。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.collection_asset_schemas import (
    ActionSpecCreate,
    ActionSpecOut,
    CollectionPlanCreate,
    CollectionPlanOut,
    CollectionSopCreate,
    CollectionSopOut,
    ScenarioCreate,
    ScenarioOut,
)
from app.api.v1.collection_asset_write_routes import router as write_router
from app.api.v1.collection_dependencies import CollectionUseCasesDep
from app.api.v1.schemas import PageOut
from app.application.collection import (
    NewActionSpec,
    NewCollectionPlan,
    NewCollectionSop,
    NewScenario,
)
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["collection-assets"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


# --- 采集方案 ---------------------------------------------------------------
@router.get("/collection-plans", response_model=PageOut[CollectionPlanOut])
def list_collection_plans(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[CollectionPlanOut]:
    page = use_cases.plans.list(scope, status=status, cursor=cursor, limit=limit)
    return PageOut(
        items=[CollectionPlanOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/collection-plans", response_model=CollectionPlanOut, status_code=201)
def create_collection_plan(
    payload: CollectionPlanCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CollectionPlanOut:
    plan = use_cases.plans.create(
        scope,
        NewCollectionPlan(
            code=payload.code,
            name=payload.name,
            plan_version=payload.plan_version,
            description=payload.description,
            owner_id=payload.owner_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionPlanOut.from_domain(plan)


@router.get("/collection-plans/{plan_id}", response_model=CollectionPlanOut)
def get_collection_plan(
    plan_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> CollectionPlanOut:
    return CollectionPlanOut.from_domain(use_cases.plans.get(scope, plan_id))


# --- SOP --------------------------------------------------------------------
@router.get("/collection-sops", response_model=PageOut[CollectionSopOut])
def list_collection_sops(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[CollectionSopOut]:
    page = use_cases.sops.list(scope, status=status, cursor=cursor, limit=limit)
    return PageOut(
        items=[CollectionSopOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/collection-sops", response_model=CollectionSopOut, status_code=201)
def create_collection_sop(
    payload: CollectionSopCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CollectionSopOut:
    sop = use_cases.sops.create(
        scope,
        NewCollectionSop(
            code=payload.code,
            name=payload.name,
            sop_version=payload.sop_version,
            steps=tuple(payload.steps),
            description=payload.description,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionSopOut.from_domain(sop)


@router.get("/collection-sops/{sop_id}", response_model=CollectionSopOut)
def get_collection_sop(
    sop_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> CollectionSopOut:
    return CollectionSopOut.from_domain(use_cases.sops.get(scope, sop_id))


# --- 场景 -------------------------------------------------------------------
@router.get("/scenarios", response_model=PageOut[ScenarioOut])
def list_scenarios(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[ScenarioOut]:
    page = use_cases.scenarios.list(scope, status=status, cursor=cursor, limit=limit)
    return PageOut(
        items=[ScenarioOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(
    payload: ScenarioCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> ScenarioOut:
    scenario = use_cases.scenarios.create(
        scope,
        NewScenario(
            code=payload.code,
            name=payload.name,
            scene_labels=tuple(payload.scene_labels),
            description=payload.description,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return ScenarioOut.from_domain(scenario)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(
    scenario_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> ScenarioOut:
    return ScenarioOut.from_domain(use_cases.scenarios.get(scope, scenario_id))


# --- 动作库 -----------------------------------------------------------------
@router.get("/action-specs", response_model=PageOut[ActionSpecOut])
def list_action_specs(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> PageOut[ActionSpecOut]:
    page = use_cases.action_specs.list(scope, status=status, cursor=cursor, limit=limit)
    return PageOut(
        items=[ActionSpecOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/action-specs", response_model=ActionSpecOut, status_code=201)
def create_action_spec(
    payload: ActionSpecCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> ActionSpecOut:
    spec = use_cases.action_specs.create(
        scope,
        NewActionSpec(
            code=payload.code,
            name=payload.name,
            spec_version=payload.spec_version,
            actions=tuple(payload.actions),
            description=payload.description,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return ActionSpecOut.from_domain(spec)


@router.get("/action-specs/{spec_id}", response_model=ActionSpecOut)
def get_action_spec(
    spec_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> ActionSpecOut:
    return ActionSpecOut.from_domain(use_cases.action_specs.get(scope, spec_id))


# 写路径端点挂到同一个 router 上，保证前缀与 tag 一致。
router.include_router(write_router)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
