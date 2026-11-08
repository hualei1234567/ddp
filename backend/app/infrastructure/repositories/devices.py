"""机器人、设备与传感器 Repository 的 SQLAlchemy 实现。

标定配置/标定记录已拆分到 :mod:`app.infrastructure.repositories.calibration_records`，
本模块保留兼容性再导出。

所有读写都以服务端构造的 :class:`ProjectScope` 为准：
- 写入前 ``scope.ensure(entity.project_id)``；
- 按 ID/编码查询始终附加 ``project_id`` 条件，跨项目返回 ``None``；
- 唯一约束冲突映射为 :class:`DuplicateResourceError`；
- 乐观锁失败映射为 :class:`VersionConflictError`。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.devices.enums import (
    DeviceType,
    LifecycleStatus,
    SensorSyncMode,
    SensorType,
)
from app.domain.devices.models import Device, Robot, Sensor
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import Device as DeviceModel
from app.infrastructure.db.models.devices import Robot as RobotModel
from app.infrastructure.db.models.devices import Sensor as SensorModel
from app.infrastructure.repositories._sql_support import (
    bounded_limit,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)
from app.infrastructure.repositories.calibration_records import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
)

__all__ = [
    "SqlCalibrationProfileRepository",
    "SqlCalibrationRecordRepository",
    "SqlDeviceRepository",
    "SqlRobotRepository",
    "SqlSensorRepository",
]


def _robot_values(robot: Robot, *, actor_id: UUID) -> dict[str, object]:
    return {
        "code": robot.code,
        "name": robot.name,
        "robot_type": robot.robot_type,
        "status": enum_value(robot.status),
        "vendor": robot.vendor,
        "model": robot.model,
        "serial_number": robot.serial_number,
        "firmware_version": robot.firmware_version,
        "hardware_version": robot.hardware_version,
        "calibration_profile_id": robot.calibration_profile_id,
        "capability_profile": dict(robot.capability_profile),
        "location_id": robot.location_id,
        "owner_user_id": robot.owner_user_id,
        "updated_by": actor_id,
    }


def _device_values(device: Device, *, actor_id: UUID) -> dict[str, object]:
    return {
        "code": device.code,
        "name": device.name,
        "device_type": enum_value(device.device_type),
        "robot_id": device.robot_id,
        "status": enum_value(device.status),
        "vendor": device.vendor,
        "model": device.model,
        "serial_number": device.serial_number,
        "firmware_version": device.firmware_version,
        "hardware_version": device.hardware_version,
        "calibration_profile_id": device.calibration_profile_id,
        "capability_profile": dict(device.capability_profile),
        "location_id": device.location_id,
        "owner_user_id": device.owner_user_id,
        "updated_by": actor_id,
    }


def _sensor_values(sensor: Sensor, *, actor_id: UUID) -> dict[str, object]:
    return {
        "code": sensor.code,
        "sensor_type": enum_value(sensor.sensor_type),
        "robot_id": sensor.robot_id,
        "device_id": sensor.device_id,
        "status": enum_value(sensor.status),
        "vendor": sensor.vendor,
        "model": sensor.model,
        "serial_number": sensor.serial_number,
        "sample_rate_hz": sensor.sample_rate_hz,
        "resolution_w": sensor.resolution_w,
        "resolution_h": sensor.resolution_h,
        "bit_depth": sensor.bit_depth,
        "channels": sensor.channels,
        "intrinsic_calibration_id": sensor.intrinsic_calibration_id,
        "extrinsic_calibration_id": sensor.extrinsic_calibration_id,
        "coordinate_frame": sensor.coordinate_frame,
        "sync_mode": enum_value(sensor.sync_mode),
        "sync_group": sensor.sync_group,
        "firmware_version": sensor.firmware_version,
        "calibration_profile_id": sensor.calibration_profile_id,
        "mount_position": sensor.mount_position,
        "updated_by": actor_id,
    }


def _robot_from_row(row: RobotModel) -> Robot:
    return Robot(
        robot_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        robot_type=row.robot_type,
        status=LifecycleStatus(row.status),
        vendor=row.vendor,
        model=row.model,
        serial_number=row.serial_number,
        firmware_version=row.firmware_version,
        hardware_version=row.hardware_version,
        calibration_profile_id=row.calibration_profile_id,
        capability_profile=row.capability_profile,
        location_id=row.location_id,
        owner_user_id=row.owner_user_id,
        version=row.version,
    )


def _device_from_row(row: DeviceModel) -> Device:
    return Device(
        device_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        device_type=DeviceType(row.device_type),
        robot_id=row.robot_id,
        status=LifecycleStatus(row.status),
        vendor=row.vendor,
        model=row.model,
        serial_number=row.serial_number,
        firmware_version=row.firmware_version,
        hardware_version=row.hardware_version,
        calibration_profile_id=row.calibration_profile_id,
        capability_profile=row.capability_profile,
        location_id=row.location_id,
        owner_user_id=row.owner_user_id,
        version=row.version,
    )


def _sensor_from_row(row: SensorModel) -> Sensor:
    return Sensor(
        sensor_id=row.id,
        project_id=row.project_id,
        code=row.code,
        sensor_type=SensorType(row.sensor_type),
        robot_id=row.robot_id,
        device_id=row.device_id,
        status=LifecycleStatus(row.status),
        vendor=row.vendor,
        model=row.model,
        serial_number=row.serial_number,
        sample_rate_hz=row.sample_rate_hz,
        resolution_w=row.resolution_w,
        resolution_h=row.resolution_h,
        bit_depth=row.bit_depth,
        channels=row.channels,
        intrinsic_calibration_id=row.intrinsic_calibration_id,
        extrinsic_calibration_id=row.extrinsic_calibration_id,
        coordinate_frame=row.coordinate_frame,
        sync_mode=SensorSyncMode(row.sync_mode),
        sync_group=row.sync_group,
        firmware_version=row.firmware_version,
        calibration_profile_id=row.calibration_profile_id,
        mount_position=row.mount_position,
        version=row.version,
    )


class SqlRobotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, robot: Robot) -> Robot:
        scope.ensure(robot.project_id)
        ensure_initial_version(robot.version)
        row = RobotModel(
            id=robot.robot_id,
            project_id=robot.project_id,
            created_by=scope.actor_id,
            **_robot_values(robot, actor_id=scope.actor_id),
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return robot

    def get(self, scope: ProjectScope, robot_id: UUID) -> Robot | None:
        row = self._session.scalar(
            select(RobotModel).where(
                RobotModel.id == robot_id,
                RobotModel.project_id == scope.project_id,
            )
        )
        return _robot_from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> Robot | None:
        row = self._session.scalar(
            select(RobotModel).where(
                RobotModel.code == code.strip(),
                RobotModel.project_id == scope.project_id,
            )
        )
        return _robot_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Robot]:
        statement = scoped_select(RobotModel, scope)
        if status is not None:
            statement = statement.where(RobotModel.status == status.value)
        if after_id is not None:
            statement = statement.where(RobotModel.id > after_id)
        statement = statement.order_by(RobotModel.id).limit(bounded_limit(limit, maximum=101))
        return [_robot_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        robot: Robot,
        *,
        expected_version: int,
    ) -> Robot:
        scope.ensure(robot.project_id)
        ensure_expected_version(robot.version, expected_version)
        result = self._session.execute(
            update(RobotModel)
            .where(
                RobotModel.id == robot.robot_id,
                RobotModel.project_id == scope.project_id,
                RobotModel.version == expected_version,
            )
            .values(
                **_robot_values(robot, actor_id=scope.actor_id),
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                RobotModel,
                scope,
                robot.robot_id,
                "机器人",
                expected_version,
            )
        return replace(robot, version=expected_version + 1)


class SqlDeviceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, device: Device) -> Device:
        scope.ensure(device.project_id)
        ensure_initial_version(device.version)
        row = DeviceModel(
            id=device.device_id,
            project_id=device.project_id,
            created_by=scope.actor_id,
            **_device_values(device, actor_id=scope.actor_id),
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return device

    def get(self, scope: ProjectScope, device_id: UUID) -> Device | None:
        row = self._session.scalar(
            select(DeviceModel).where(
                DeviceModel.id == device_id,
                DeviceModel.project_id == scope.project_id,
            )
        )
        return _device_from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> Device | None:
        row = self._session.scalar(
            select(DeviceModel).where(
                DeviceModel.code == code.strip(),
                DeviceModel.project_id == scope.project_id,
            )
        )
        return _device_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Device]:
        statement = scoped_select(DeviceModel, scope)
        if status is not None:
            statement = statement.where(DeviceModel.status == status.value)
        if after_id is not None:
            statement = statement.where(DeviceModel.id > after_id)
        statement = statement.order_by(DeviceModel.id).limit(bounded_limit(limit, maximum=101))
        return [_device_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        device: Device,
        *,
        expected_version: int,
    ) -> Device:
        scope.ensure(device.project_id)
        ensure_expected_version(device.version, expected_version)
        result = self._session.execute(
            update(DeviceModel)
            .where(
                DeviceModel.id == device.device_id,
                DeviceModel.project_id == scope.project_id,
                DeviceModel.version == expected_version,
            )
            .values(
                **_device_values(device, actor_id=scope.actor_id),
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                DeviceModel,
                scope,
                device.device_id,
                "设备",
                expected_version,
            )
        return replace(device, version=expected_version + 1)


class SqlSensorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, sensor: Sensor) -> Sensor:
        scope.ensure(sensor.project_id)
        ensure_initial_version(sensor.version)
        row = SensorModel(
            id=sensor.sensor_id,
            project_id=sensor.project_id,
            created_by=scope.actor_id,
            **_sensor_values(sensor, actor_id=scope.actor_id),
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return sensor

    def get(self, scope: ProjectScope, sensor_id: UUID) -> Sensor | None:
        row = self._session.scalar(
            select(SensorModel).where(
                SensorModel.id == sensor_id,
                SensorModel.project_id == scope.project_id,
            )
        )
        return _sensor_from_row(row) if row is not None else None

    def get_by_code(self, scope: ProjectScope, code: str) -> Sensor | None:
        row = self._session.scalar(
            select(SensorModel).where(
                SensorModel.code == code.strip(),
                SensorModel.project_id == scope.project_id,
            )
        )
        return _sensor_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        sensor_type: SensorType | None = None,
        robot_id: UUID | None = None,
        device_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Sensor]:
        statement = scoped_select(SensorModel, scope)
        if status is not None:
            statement = statement.where(SensorModel.status == status.value)
        if sensor_type is not None:
            statement = statement.where(SensorModel.sensor_type == sensor_type.value)
        if robot_id is not None:
            statement = statement.where(SensorModel.robot_id == robot_id)
        if device_id is not None:
            statement = statement.where(SensorModel.device_id == device_id)
        if after_id is not None:
            statement = statement.where(SensorModel.id > after_id)
        statement = statement.order_by(SensorModel.id).limit(bounded_limit(limit, maximum=101))
        return [_sensor_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        sensor: Sensor,
        *,
        expected_version: int,
    ) -> Sensor:
        scope.ensure(sensor.project_id)
        ensure_expected_version(sensor.version, expected_version)
        result = self._session.execute(
            update(SensorModel)
            .where(
                SensorModel.id == sensor.sensor_id,
                SensorModel.project_id == scope.project_id,
                SensorModel.version == expected_version,
            )
            .values(
                **_sensor_values(sensor, actor_id=scope.actor_id),
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                SensorModel,
                scope,
                sensor.sensor_id,
                "传感器",
                expected_version,
            )
        return replace(sensor, version=expected_version + 1)
