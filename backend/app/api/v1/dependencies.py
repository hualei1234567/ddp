"""设备域 API 依赖装配：把 SQL 仓储与会话注入应用用例。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.devices import (
    CalibrationProfileUseCases,
    CalibrationUseCases,
    DeviceUseCases,
    RobotUseCases,
    SensorUseCases,
)
from app.infrastructure.repositories.calibration_records import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
)
from app.infrastructure.repositories.dataset_audit import (
    SqlDatasetAuditRepository,
    SqlDatasetOutboxRepository,
)
from app.infrastructure.repositories.devices import (
    SqlDeviceRepository,
    SqlRobotRepository,
    SqlSensorRepository,
)
from app.infrastructure.repositories.idempotency import SqlIdempotencyRepository


def get_device_use_cases(session: DbSession) -> DeviceUseCases:
    return DeviceUseCases(
        devices=SqlDeviceRepository(session),
        robots=SqlRobotRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        idempotency=SqlIdempotencyRepository(session),
    )


def get_sensor_use_cases(session: DbSession) -> SensorUseCases:
    return SensorUseCases(
        sensors=SqlSensorRepository(session),
        devices=SqlDeviceRepository(session),
        robots=SqlRobotRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        calibrations=SqlCalibrationRecordRepository(session),
        uow=session,
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        idempotency=SqlIdempotencyRepository(session),
    )


def get_calibration_use_cases(session: DbSession) -> CalibrationUseCases:
    return CalibrationUseCases(
        calibrations=SqlCalibrationRecordRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
    )


def get_robot_use_cases(session: DbSession) -> RobotUseCases:
    return RobotUseCases(
        robots=SqlRobotRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        idempotency=SqlIdempotencyRepository(session),
    )


def get_calibration_profile_use_cases(session: DbSession) -> CalibrationProfileUseCases:
    return CalibrationProfileUseCases(
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
        audit=SqlDatasetAuditRepository(session),
        outbox=SqlDatasetOutboxRepository(session),
        idempotency=SqlIdempotencyRepository(session),
    )


DeviceUseCasesDep = Annotated[DeviceUseCases, Depends(get_device_use_cases)]
RobotUseCasesDep = Annotated[RobotUseCases, Depends(get_robot_use_cases)]
CalibrationProfileUseCasesDep = Annotated[
    CalibrationProfileUseCases,
    Depends(get_calibration_profile_use_cases),
]
SensorUseCasesDep = Annotated[SensorUseCases, Depends(get_sensor_use_cases)]
CalibrationUseCasesDep = Annotated[
    CalibrationUseCases,
    Depends(get_calibration_use_cases),
]
