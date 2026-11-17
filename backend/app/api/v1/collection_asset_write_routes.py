"""采集素材写路径 HTTP 路由：PATCH / 状态动作 / DELETE（N8）。

为什么与 ``collection_asset_routes.py`` 分开
--------------------------------------------
N8 给四类素材各补了三个写端点（共 12 个），把它们全部塞进原文件会让它越过
500 行的评审阈值。这里按"读+创建 / 写"拆分而不是按素材类型拆分：写路径共享
`If-Match`、`Idempotency-Key`、`DeleteScope` 三项协议约束，放在一起才看得出
它们的一致性；按素材拆会把同一段协议适配复制四遍。

端点的实际操作都委托给 ``collection_asset_routes`` 里暴露的同一个 ``router``
实例（``include_router`` 时共用前缀与 tag），因此对外路径不变：
``/projects/{project_id}/collection-plans|collection-sops|scenarios|action-specs``。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.deps import get_delete_scope, get_write_scope
from app.api.v1.collection_asset_schemas import (
    ActionSpecOut,
    ActionSpecPatch,
    AssetActionRequest,
    CollectionPlanOut,
    CollectionPlanPatch,
    CollectionSopOut,
    CollectionSopPatch,
    ScenarioOut,
    ScenarioPatch,
)
from app.api.v1.collection_dependencies import CollectionUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.domain.identity.models import ProjectScope

# 与读路径共用一个 router 实例：main 里只 include 一次，路径前缀与 OpenAPI tag
# 不会分叉。
router = APIRouter()

WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
DeleteScope = Annotated[ProjectScope, Depends(get_delete_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]


# --- 采集方案 ---------------------------------------------------------------
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


__all__ = ["router"]
