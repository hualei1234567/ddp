"""传感器 API：项目作用域列表、详情、创建、修改与生命周期动作。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.dependencies import SensorUseCasesDep
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import (
    ActionRequest,
    PageOut,
    SensorCreate,
    SensorOut,
    SensorPatch,
)
from app.application.devices import NewSensor
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}/sensors", tags=["sensors"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]


@router.get("", response_model=PageOut[SensorOut])
def list_sensors(
    scope: ReadScope,
    use_cases: SensorUseCasesDep,
    status: str | None = None,
    sensor_type: str | None = None,
    robot_id: UUID | None = None,
    device_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[SensorOut]:
    page = use_cases.list_sensors(
        scope,
        status=status,
        sensor_type=sensor_type,
        robot_id=robot_id,
        device_id=device_id,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[SensorOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=SensorOut, status_code=201)
def create_sensor(
    payload: SensorCreate,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
) -> SensorOut:
    sensor = use_cases.create_sensor(scope, NewSensor(**payload.model_dump()))
    return SensorOut.from_domain(sensor)


@router.get("/{sensor_id}", response_model=SensorOut)
def get_sensor(
    sensor_id: UUID,
    scope: ReadScope,
    use_cases: SensorUseCasesDep,
) -> SensorOut:
    return SensorOut.from_domain(use_cases.get_sensor(scope, sensor_id))


@router.patch("/{sensor_id}", response_model=SensorOut)
def update_sensor(
    sensor_id: UUID,
    payload: SensorPatch,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
) -> SensorOut:
    sensor = use_cases.update_sensor(
        scope,
        sensor_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
    )
    return SensorOut.from_domain(sensor)


@router.post("/{sensor_id}/activate", response_model=SensorOut)
def activate_sensor(
    sensor_id: UUID,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
) -> SensorOut:
    return _action(use_cases, scope, sensor_id, "activate", expected_version, payload)


@router.post("/{sensor_id}/maintenance", response_model=SensorOut)
def enter_maintenance(
    sensor_id: UUID,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
) -> SensorOut:
    return _action(
        use_cases,
        scope,
        sensor_id,
        "enter-maintenance",
        expected_version,
        payload,
    )


@router.post("/{sensor_id}/fault", response_model=SensorOut)
def report_fault(
    sensor_id: UUID,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
) -> SensorOut:
    return _action(
        use_cases,
        scope,
        sensor_id,
        "report-fault",
        expected_version,
        payload,
    )


@router.post("/{sensor_id}/suspend", response_model=SensorOut)
def suspend_sensor(
    sensor_id: UUID,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
) -> SensorOut:
    return _action(use_cases, scope, sensor_id, "suspend", expected_version, payload)


@router.post("/{sensor_id}/retire", response_model=SensorOut)
def retire_sensor(
    sensor_id: UUID,
    scope: WriteScope,
    use_cases: SensorUseCasesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
) -> SensorOut:
    return _action(use_cases, scope, sensor_id, "retire", expected_version, payload)


def _action(
    use_cases: SensorUseCasesDep,
    scope: WriteScope,
    sensor_id: UUID,
    action: str,
    expected_version: int,
    payload: ActionRequest | None,
) -> SensorOut:
    del payload  # 阶段 0 无 audit/status_reason 列，先保持契约形状而不伪造持久化。
    sensor = use_cases.apply_action(
        scope,
        sensor_id,
        action,
        expected_version=expected_version,
    )
    return SensorOut.from_domain(sensor)
