"""设备域 API 依赖装配：把 SQL 仓储与会话注入应用用例。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession
from app.application.devices import (
    CalibrationUseCases,
    DeviceUseCases,
    SensorUseCases,
)
from app.infrastructure.repositories.calibration_records import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
)
from app.infrastructure.repositories.devices import (
    SqlDeviceRepository,
    SqlRobotRepository,
    SqlSensorRepository,
)


def get_device_use_cases(session: DbSession) -> DeviceUseCases:
    return DeviceUseCases(
        devices=SqlDeviceRepository(session),
        robots=SqlRobotRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
    )


def get_sensor_use_cases(session: DbSession) -> SensorUseCases:
    return SensorUseCases(
        sensors=SqlSensorRepository(session),
        devices=SqlDeviceRepository(session),
        robots=SqlRobotRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        calibrations=SqlCalibrationRecordRepository(session),
        uow=session,
    )


def get_calibration_use_cases(session: DbSession) -> CalibrationUseCases:
    return CalibrationUseCases(
        calibrations=SqlCalibrationRecordRepository(session),
        profiles=SqlCalibrationProfileRepository(session),
        uow=session,
    )


DeviceUseCasesDep = Annotated[DeviceUseCases, Depends(get_device_use_cases)]
SensorUseCasesDep = Annotated[SensorUseCases, Depends(get_sensor_use_cases)]
CalibrationUseCasesDep = Annotated[
    CalibrationUseCases,
    Depends(get_calibration_use_cases),
]
