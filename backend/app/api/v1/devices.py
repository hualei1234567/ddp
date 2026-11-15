"""设备 API：项目作用域列表、详情、创建、修改与生命周期动作。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.dataset_dependencies import IdempotencyKey
from app.api.v1.dependencies import DeviceUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import (
    ActionRequest,
    DeviceCreate,
    DeviceOut,
    DevicePatch,
    PageOut,
)
from app.api.v1.supplier_dependencies import request_id
from app.application.devices import NewDevice
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}/devices", tags=["devices"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]


@router.get("", response_model=PageOut[DeviceOut])
def list_devices(
    scope: ReadScope,
    use_cases: DeviceUseCasesDep,
    status: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[DeviceOut]:
    page = use_cases.list_devices(
        scope,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[DeviceOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    payload: DeviceCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    device = use_cases.create_device(
        scope,
        NewDevice(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DeviceOut.from_domain(device)


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(
    device_id: UUID,
    scope: ReadScope,
    use_cases: DeviceUseCasesDep,
) -> DeviceOut:
    return DeviceOut.from_domain(use_cases.get_device(scope, device_id))


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: UUID,
    payload: DevicePatch,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
) -> DeviceOut:
    device = use_cases.update_device(
        scope,
        device_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
    )
    return DeviceOut.from_domain(device)


@router.post("/{device_id}/activate", response_model=DeviceOut)
def activate_device(
    device_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    return _action(
        use_cases, request, scope, device_id, "activate", expected_version, payload, idempotency_key
    )


@router.post("/{device_id}/maintenance", response_model=DeviceOut)
def enter_maintenance(
    device_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    return _action(
        use_cases,
        request,
        scope,
        device_id,
        "enter-maintenance",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{device_id}/fault", response_model=DeviceOut)
def report_fault(
    device_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    return _action(
        use_cases,
        request,
        scope,
        device_id,
        "report-fault",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{device_id}/suspend", response_model=DeviceOut)
def suspend_device(
    device_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    return _action(
        use_cases, request, scope, device_id, "suspend", expected_version, payload, idempotency_key
    )


@router.post("/{device_id}/retire", response_model=DeviceOut)
def retire_device(
    device_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: DeviceUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DeviceOut:
    return _action(
        use_cases, request, scope, device_id, "retire", expected_version, payload, idempotency_key
    )


def _action(
    use_cases: DeviceUseCasesDep,
    request: Request,
    scope: WriteScope,
    device_id: UUID,
    action: str,
    expected_version: int,
    payload: ActionRequest | None,
    idempotency_key: str | None,
) -> DeviceOut:
    """生命周期动作：请求体语义字段透传给用例，不再丢弃（G-10）。"""
    body = payload or ActionRequest()
    device = use_cases.apply_action(
        scope,
        device_id,
        action,
        expected_version=expected_version,
        reason=body.reason,
        fault_code=body.fault_code,
        fault_detail=body.fault_detail,
        fault_occurred_at=body.occurred_at,
        maintenance_record_id=body.maintenance_record_id,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DeviceOut.from_domain(device)
