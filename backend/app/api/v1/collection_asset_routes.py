"""采集素材 HTTP 路由：采集方案 / SOP / 场景 / 动作库（COL-002 + N8）。

路由只做协议适配（路径、请求体、If-Match、Idempotency-Key），业务规则在
``app/application/collection/assets.py`` 与 ``_asset_mutations.py``；
项目隔离与权限由服务端依赖强制。

四条路径形状一致：``POST`` 创建 + ``GET`` 列表 + ``GET`` 详情（COL-002），
N8 追加 ``PATCH`` 更新 + ``POST .../actions/{action}`` 状态动作 + ``DELETE``。
三组写端点共用同一组类型别名，不在四类素材里各写一份。

权限分层刻意与既有约定一致：
- 读：``READ``（viewer 可用）；
- 更新与状态动作：``WRITE``（editor 起）；
- 删除：``DELETE``（仅 owner / 平台管理员），与 ``get_delete_scope`` 的既有语义
  一致——删除是不可逆动作，不交给普通编辑者。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.api.deps import get_delete_scope, get_project_scope, get_write_scope
from app.api.v1.collection_asset_schemas import (
    ActionSpecCreate,
    ActionSpecOut,
    ActionSpecPatch,
    AssetActionRequest,
    CollectionPlanCreate,
    CollectionPlanOut,
    CollectionPlanPatch,
    CollectionSopCreate,
    CollectionSopOut,
    CollectionSopPatch,
    ScenarioCreate,
    ScenarioOut,
    ScenarioPatch,
)
from app.api.v1.collection_dependencies import CollectionUseCasesDep
from app.api.v1.preconditions import require_expected_version
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
DeleteScope = Annotated[ProjectScope, Depends(get_delete_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
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


@router.patch("/collection-plans/{plan_id}", response_model=CollectionPlanOut)
def update_collection_plan(
    plan_id: UUID,
    payload: CollectionPlanPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> CollectionPlanOut:
    plan = use_cases.plans.update(
        scope,
        plan_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return CollectionPlanOut.from_domain(plan)


@router.post(
    "/collection-plans/{plan_id}/actions/{action}",
    response_model=CollectionPlanOut,
)
def act_on_collection_plan(
    plan_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AssetActionRequest | None = None,
) -> CollectionPlanOut:
    plan = use_cases.plans.apply_action(
        scope,
        plan_id,
        action,
        expected_version=expected_version,
        payload=_action_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionPlanOut.from_domain(plan)


@router.delete("/collection-plans/{plan_id}", status_code=204)
def delete_collection_plan(
    plan_id: UUID,
    request: Request,
    scope: DeleteScope,
    use_cases: CollectionUseCasesDep,
) -> Response:
    use_cases.plans.delete(scope, plan_id, request_id=_request_id(request))
    return Response(status_code=204)


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


@router.patch("/collection-sops/{sop_id}", response_model=CollectionSopOut)
def update_collection_sop(
    sop_id: UUID,
    payload: CollectionSopPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> CollectionSopOut:
    sop = use_cases.sops.update(
        scope,
        sop_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return CollectionSopOut.from_domain(sop)


@router.post(
    "/collection-sops/{sop_id}/actions/{action}",
    response_model=CollectionSopOut,
)
def act_on_collection_sop(
    sop_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AssetActionRequest | None = None,
) -> CollectionSopOut:
    sop = use_cases.sops.apply_action(
        scope,
        sop_id,
        action,
        expected_version=expected_version,
        payload=_action_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionSopOut.from_domain(sop)


@router.delete("/collection-sops/{sop_id}", status_code=204)
def delete_collection_sop(
    sop_id: UUID,
    request: Request,
    scope: DeleteScope,
    use_cases: CollectionUseCasesDep,
) -> Response:
    use_cases.sops.delete(scope, sop_id, request_id=_request_id(request))
    return Response(status_code=204)


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


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(
    scenario_id: UUID,
    payload: ScenarioPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> ScenarioOut:
    scenario = use_cases.scenarios.update(
        scope,
        scenario_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return ScenarioOut.from_domain(scenario)


@router.post(
    "/scenarios/{scenario_id}/actions/{action}",
    response_model=ScenarioOut,
)
def act_on_scenario(
    scenario_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AssetActionRequest | None = None,
) -> ScenarioOut:
    scenario = use_cases.scenarios.apply_action(
        scope,
        scenario_id,
        action,
        expected_version=expected_version,
        payload=_action_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return ScenarioOut.from_domain(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(
    scenario_id: UUID,
    request: Request,
    scope: DeleteScope,
    use_cases: CollectionUseCasesDep,
) -> Response:
    use_cases.scenarios.delete(scope, scenario_id, request_id=_request_id(request))
    return Response(status_code=204)


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


@router.patch("/action-specs/{spec_id}", response_model=ActionSpecOut)
def update_action_spec(
    spec_id: UUID,
    payload: ActionSpecPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> ActionSpecOut:
    spec = use_cases.action_specs.update(
        scope,
        spec_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return ActionSpecOut.from_domain(spec)


@router.post(
    "/action-specs/{spec_id}/actions/{action}",
    response_model=ActionSpecOut,
)
def act_on_action_spec(
    spec_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AssetActionRequest | None = None,
) -> ActionSpecOut:
    spec = use_cases.action_specs.apply_action(
        scope,
        spec_id,
        action,
        expected_version=expected_version,
        payload=_action_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return ActionSpecOut.from_domain(spec)


@router.delete("/action-specs/{spec_id}", status_code=204)
def delete_action_spec(
    spec_id: UUID,
    request: Request,
    scope: DeleteScope,
    use_cases: CollectionUseCasesDep,
) -> Response:
    use_cases.action_specs.delete(scope, spec_id, request_id=_request_id(request))
    return Response(status_code=204)


def _action_payload(payload: AssetActionRequest | None) -> dict[str, object] | None:
    """只把客户端**真正提交**的键传给用例；未提交与 ``null`` 语义不同。"""
    if payload is None:
        return None
    return payload.model_dump(exclude_unset=True)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
