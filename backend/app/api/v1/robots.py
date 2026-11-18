"""机器人 API：项目作用域列表、详情、创建、修改与生命周期动作（G-01）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.dataset_dependencies import IdempotencyKey
from app.api.v1.dependencies import RobotUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import (
    ActionRequest,
    PageOut,
    RobotCreate,
    RobotOut,
    RobotPatch,
)
from app.api.v1.supplier_dependencies import request_id
from app.application.devices import NewRobot
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}/robots", tags=["robots"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]


@router.get("", response_model=PageOut[RobotOut])
def list_robots(
    scope: ReadScope,
    use_cases: RobotUseCasesDep,
    status: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[RobotOut]:
    page = use_cases.list_robots(scope, status=status, cursor=cursor, limit=limit)
    return PageOut(
        items=[RobotOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=RobotOut, status_code=201)
def create_robot(
    payload: RobotCreate,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    robot = use_cases.create_robot(
        scope,
        NewRobot(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return RobotOut.from_domain(robot)


@router.get("/{robot_id}", response_model=RobotOut)
def get_robot(
    robot_id: UUID,
    scope: ReadScope,
    use_cases: RobotUseCasesDep,
) -> RobotOut:
    return RobotOut.from_domain(use_cases.get_robot(scope, robot_id))


@router.patch("/{robot_id}", response_model=RobotOut)
def update_robot(
    robot_id: UUID,
    payload: RobotPatch,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
) -> RobotOut:
    robot = use_cases.update_robot(
        scope,
        robot_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=request_id(request),
    )
    return RobotOut.from_domain(robot)


def _action(
    use_cases: RobotUseCasesDep,
    request: Request,
    scope: WriteScope,
    robot_id: UUID,
    action: str,
    expected_version: int,
    payload: ActionRequest | None,
    idempotency_key: str | None,
) -> RobotOut:
    """生命周期动作：请求体语义字段透传给用例（与 Device 一致）。"""
    body = payload or ActionRequest()
    robot = use_cases.apply_action(
        scope,
        robot_id,
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
    return RobotOut.from_domain(robot)


@router.post("/{robot_id}/activate", response_model=RobotOut)
def activate_robot(
    robot_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    return _action(
        use_cases,
        request,
        scope,
        robot_id,
        "activate",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{robot_id}/maintenance", response_model=RobotOut)
def enter_maintenance(
    robot_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    return _action(
        use_cases,
        request,
        scope,
        robot_id,
        "enter-maintenance",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{robot_id}/fault", response_model=RobotOut)
def report_fault(
    robot_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    return _action(
        use_cases,
        request,
        scope,
        robot_id,
        "report-fault",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{robot_id}/suspend", response_model=RobotOut)
def suspend_robot(
    robot_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    return _action(
        use_cases,
        request,
        scope,
        robot_id,
        "suspend",
        expected_version,
        payload,
        idempotency_key,
    )


@router.post("/{robot_id}/retire", response_model=RobotOut)
def retire_robot(
    robot_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: RobotUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RobotOut:
    return _action(
        use_cases,
        request,
        scope,
        robot_id,
        "retire",
        expected_version,
        payload,
        idempotency_key,
    )


__all__ = ["router"]
