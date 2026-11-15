"""标定记录 API：项目作用域查询、草稿写入与状态动作。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_scope, get_scope, get_write_scope
from app.api.v1.dataset_dependencies import request_id
from app.api.v1.dependencies import CalibrationUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import (
    CalibrationCreate,
    CalibrationOut,
    CalibrationPatch,
    PageOut,
)
from app.application.devices import NewCalibration
from app.domain.identity.models import ProjectAction, ProjectScope

router = APIRouter(
    prefix="/projects/{project_id}/calibrations",
    tags=["calibrations"],
)

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ValidateScope = Annotated[
    ProjectScope,
    Depends(get_scope(ProjectAction.VALIDATE)),
]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]


@router.get("", response_model=PageOut[CalibrationOut])
def list_calibrations(
    scope: ReadScope,
    use_cases: CalibrationUseCasesDep,
    target_type: str | None = None,
    target_id: UUID | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[CalibrationOut]:
    page = use_cases.list_calibrations(
        scope,
        target_type=target_type,
        target_id=target_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[CalibrationOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=CalibrationOut, status_code=201)
def create_calibration(
    payload: CalibrationCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
) -> CalibrationOut:
    values = payload.model_dump()
    values["evidence_asset_ids"] = tuple(payload.evidence_asset_ids)
    record = use_cases.create_calibration(
        scope,
        NewCalibration(**values),
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.get("/{calibration_id}", response_model=CalibrationOut)
def get_calibration(
    calibration_id: UUID,
    scope: ReadScope,
    use_cases: CalibrationUseCasesDep,
) -> CalibrationOut:
    return CalibrationOut.from_domain(use_cases.get_calibration(scope, calibration_id))


@router.patch("/{calibration_id}", response_model=CalibrationOut)
def update_calibration(
    calibration_id: UUID,
    payload: CalibrationPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    changes = payload.model_dump(exclude_unset=True)
    if isinstance(changes.get("evidence_asset_ids"), list):
        changes["evidence_asset_ids"] = tuple(changes["evidence_asset_ids"])
    record = use_cases.update_calibration(
        scope,
        calibration_id,
        expected_version=expected_version,
        changes=changes,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/submit", response_model=CalibrationOut)
def submit_calibration(
    calibration_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.submit(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/validate", response_model=CalibrationOut)
def validate_calibration(
    calibration_id: UUID,
    request: Request,
    scope: ValidateScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.validate(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/reject", response_model=CalibrationOut)
def reject_calibration(
    calibration_id: UUID,
    request: Request,
    scope: ValidateScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.reject(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/activate", response_model=CalibrationOut)
def activate_calibration(
    calibration_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.activate(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/revoke", response_model=CalibrationOut)
def revoke_calibration(
    calibration_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.revoke(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)


@router.post("/{calibration_id}/expire", response_model=CalibrationOut)
def expire_calibration(
    calibration_id: UUID,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationUseCasesDep,
    expected_version: ExpectedVersion,
) -> CalibrationOut:
    record = use_cases.expire(
        scope,
        calibration_id,
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return CalibrationOut.from_domain(record)
