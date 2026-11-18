"""供应商档案 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.schemas import PageOut
from app.api.v1.supplier_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    SupplierServicesDep,
    WriteScope,
    request_id,
)
from app.api.v1.supplier_response_models import (
    SupplierMetricsOut,
    SupplierOut,
    SupplierPortfolioOut,
)
from app.api.v1.supplier_schemas import (
    SupplierActionRequest,
    SupplierCreate,
    SupplierPatch,
)
from app.application.suppliers.commands import NewSupplier
from app.application.suppliers.commands import SupplierPatch as SupplierPatchCmd
from app.core.errors import ForbiddenError
from app.domain.identity.models import ProjectContext, ProjectRole

router = APIRouter(prefix="/projects/{project_id}/suppliers", tags=["suppliers"])


@router.get("/portfolio", response_model=SupplierPortfolioOut)
def get_supplier_portfolio(
    scope: ReadScope,
    services: SupplierServicesDep,
    status: Annotated[str | None, Query(max_length=32)] = None,
    category: Annotated[str | None, Query(max_length=32)] = None,
) -> SupplierPortfolioOut:
    """本项目所有供应商的横向汇总。

    单供应商的 ``/{supplier_id}/metrics`` 只能回答"这一家怎么样"；运营要问的
    是"这批供应商里谁准时率最低""本月整体一次通过率多少"，那需要横向视图。

    口径与单供应商视图**完全一致**（同一份 `summarize`），不另算一套。

    比率字段为 `null` 表示没有任何供应商在该指标上有可计算数据，
    **不是整体为 0**。`suppliers_without_settled_batches` 单独给出"本期没交付"
    的家数——把这类供应商从列表里删掉会让"没交付"这件事看不见。

    路由必须声明在 `/{supplier_id}/...` 之前：FastAPI 按声明顺序匹配，
    否则 `portfolio` 会被当作 UUID 解析失败。
    """
    portfolio = services.suppliers.portfolio(scope, status=status, category=category)
    payload = portfolio.to_payload()
    return SupplierPortfolioOut.model_validate(payload)


@router.get("", response_model=PageOut[SupplierOut])
def list_suppliers(
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    category: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[SupplierOut]:
    items = services.suppliers.list(
        scope,
        status=status,
        category=category,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.supplier_id)
    return PageOut(
        items=[SupplierOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=SupplierOut, status_code=201)
def create_supplier(
    payload: SupplierCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> SupplierOut:
    supplier = services.suppliers.create(
        scope,
        NewSupplier(
            code=payload.code,
            name=payload.name,
            category=payload.category,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            external_ref=payload.external_ref,
            notes=payload.notes,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return SupplierOut.from_domain(supplier)


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: UUID, scope: ReadScope, services: SupplierServicesDep) -> SupplierOut:
    return SupplierOut.from_domain(services.suppliers.get(scope, supplier_id))


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: UUID,
    payload: SupplierPatch,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
) -> SupplierOut:
    supplier = services.suppliers.update(
        scope,
        supplier_id,
        SupplierPatchCmd(**payload.model_dump(exclude_unset=True)),
        expected_version=expected_version,
    )
    return SupplierOut.from_domain(supplier)


@router.post("/{supplier_id}/actions/{action}", response_model=SupplierOut)
def act_on_supplier(
    supplier_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
    payload: SupplierActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> SupplierOut:
    """激活/暂停/恢复为常规写动作；终止是不可逆高风险动作，要求项目 Owner。"""
    if (
        action == "terminate"
        and not context.is_platform_admin
        and context.role is not ProjectRole.OWNER
    ):
        raise ForbiddenError("终止供应商要求项目负责人权限")
    supplier = services.suppliers.apply_action(
        scope,
        supplier_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return SupplierOut.from_domain(supplier)


@router.get("/{supplier_id}/metrics", response_model=SupplierMetricsOut)
def get_supplier_metrics(
    supplier_id: UUID, scope: ReadScope, services: SupplierServicesDep
) -> SupplierMetricsOut:
    return SupplierMetricsOut.from_domain(services.suppliers.metrics(scope, supplier_id))


__all__ = ["router"]
