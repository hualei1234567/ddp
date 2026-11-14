"""供应商档案 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.supplier_dependencies import (
    ExpectedVersion,
    ReadScope,
    SupplierServicesDep,
    WriteScope,
    request_id,
)
from app.api.v1.supplier_schemas import (
    SupplierActionRequest,
    SupplierCreate,
    SupplierPatch,
    metrics_out,
    supplier_out,
)
from app.application.suppliers.commands import NewSupplier
from app.application.suppliers.commands import SupplierPatch as SupplierPatchCmd
from app.core.errors import ForbiddenError
from app.domain.identity.models import ProjectContext, ProjectRole

router = APIRouter(prefix="/projects/{project_id}/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(
    scope: ReadScope,
    services: SupplierServicesDep,
    status: str | None = None,
    category: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.suppliers.list(
        scope,
        status=status,
        category=category,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.supplier_id)
    return {
        "items": [supplier_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("", status_code=201)
def create_supplier(
    payload: SupplierCreate,
    request: Request,
    scope: WriteScope,
    services: SupplierServicesDep,
) -> dict[str, Any]:
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
        request_id=request_id(request),
    )
    return supplier_out(supplier)


@router.get("/{supplier_id}")
def get_supplier(
    supplier_id: UUID, scope: ReadScope, services: SupplierServicesDep
) -> dict[str, Any]:
    return supplier_out(services.suppliers.get(scope, supplier_id))


@router.patch("/{supplier_id}")
def update_supplier(
    supplier_id: UUID,
    payload: SupplierPatch,
    scope: WriteScope,
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    supplier = services.suppliers.update(
        scope,
        supplier_id,
        SupplierPatchCmd(**payload.model_dump(exclude_unset=True)),
        expected_version=expected_version,
    )
    return supplier_out(supplier)


@router.post("/{supplier_id}/actions/{action}")
def act_on_supplier(
    supplier_id: UUID,
    action: str,
    payload: SupplierActionRequest | None,
    request: Request,
    scope: WriteScope,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    services: SupplierServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
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
        request_id=request_id(request),
    )
    return supplier_out(supplier)


@router.get("/{supplier_id}/metrics")
def get_supplier_metrics(
    supplier_id: UUID, scope: ReadScope, services: SupplierServicesDep
) -> dict[str, Any]:
    return metrics_out(services.suppliers.metrics(scope, supplier_id))


__all__ = ["router"]
