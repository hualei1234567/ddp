"""标定配置 API：登记新版本与查询（G-01）。

没有 PATCH：Profile 是不可变配置版本（ADR-0020 / G-07），改配置 = 新建版本。
这一点在路由层就体现为"只有一个写端点"，而不是留一个 PATCH 再由下层拒绝。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.dataset_dependencies import IdempotencyKey
from app.api.v1.dependencies import CalibrationProfileUseCasesDep
from app.api.v1.schemas import CalibrationProfileCreate, CalibrationProfileOut
from app.api.v1.supplier_dependencies import request_id
from app.application.devices import NewCalibrationProfile
from app.domain.identity.models import ProjectScope

router = APIRouter(
    prefix="/projects/{project_id}/calibration-profiles",
    tags=["calibration-profiles"],
)

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]


@router.post("", response_model=CalibrationProfileOut, status_code=201)
def create_profile(
    payload: CalibrationProfileCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CalibrationProfileUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CalibrationProfileOut:
    profile = use_cases.create_profile(
        scope,
        NewCalibrationProfile(
            name=payload.name,
            scope=payload.scope,
            target_type=payload.target_type,
            schema_version=payload.schema_version,
            required_types=tuple(payload.required_types),
            selection_rule=payload.selection_rule,
            target_id=payload.target_id,
            profile_version=payload.profile_version,
            supersedes_id=payload.supersedes_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return CalibrationProfileOut.from_domain(profile)


@router.get("/{profile_id}", response_model=CalibrationProfileOut)
def get_profile(
    profile_id: UUID,
    scope: ReadScope,
    use_cases: CalibrationProfileUseCasesDep,
) -> CalibrationProfileOut:
    return CalibrationProfileOut.from_domain(use_cases.get_profile(scope, profile_id))


__all__ = ["router"]
