"""设备域 Repository 的 SQLAlchemy 实现。

所有读写都以服务端构造的 :class:`ProjectScope` 为准：
- 写入前 ``scope.ensure(entity.project_id)``；
- 按 ID/编码查询始终附加 ``project_id`` 条件，跨项目返回 ``None``；
- 唯一约束冲突映射为 :class:`DuplicateResourceError`；
- 乐观锁失败映射为 :class:`VersionConflictError`。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationStatus,
    DeviceType,
    LifecycleStatus,
    SensorSyncMode,
    SensorType,
)
from app.domain.devices.errors import (
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.devices.models import Device, Robot, Sensor
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import (
    CalibrationProfile as CalibrationProfileModel,
)
from app.infrastructure.db.models.devices import (
    CalibrationRecord as CalibrationRecordModel,
)
from app.infrastructure.db.models.devices import Device as DeviceModel
from app.infrastructure.db.models.devices import Robot as RobotModel
from app.infrastructure.db.models.devices import Sensor as SensorModel

_T = TypeVar("_T")


def _bounded_limit(limit: int) -> int:
    if limit < 1 or limit > 100:
        raise DomainValidationError("limit 必须在 1 到 100 之间")
    return limit


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 不保留时区；读回时按 UTC 解释，避免领域层拒绝 naive 时间。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        message = str(exc.orig).lower()
        if "unique" in message or "duplicate" in message or "uq_" in message:
            raise DuplicateResourceError("项目内业务键或序列号重复") from exc
        raise


def _ensure_initial_version(version: int) -> None:
    if version != 1:
        raise DomainValidationError("新增资源 version 必须为 1")


def _ensure_expected_version(entity_version: int, expected_version: int) -> None:
    if expected_version < 1:
        raise DomainValidationError("expected_version 必须大于等于 1")
    if entity_version != expected_version:
        raise VersionConflictError("资源", expected_version)


def _scoped_select(model: type[_T], scope: ProjectScope) -> Select[tuple[_T]]:
    return select(model).where(model.project_id == scope.project_id)  # type: ignore[attr-defined]


def _raise_update_failure(
    session: Session,
    model: type[Any],
    scope: ProjectScope,
    resource_id: UUID,
    resource: str,
    expected_version: int,
) -> None:
    row = session.get(model, resource_id)
    if row is None or row.project_id != scope.project_id:
        raise ResourceNotFoundError(resource, resource_id)
    raise VersionConflictError(resource, expected_version)


def _robot_values(robot: Robot, *, actor_id: UUID) -> dict[str, object]:
    return {
        "code": robot.code,
        "name": robot.name,
        "robot_type": robot.robot_type,
        "status": _enum_value(robot.status),
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
        "device_type": _enum_value(device.device_type),
        "robot_id": device.robot_id,
        "status": _enum_value(device.status),
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
        "sensor_type": _enum_value(sensor.sensor_type),
        "robot_id": sensor.robot_id,
        "device_id": sensor.device_id,
        "status": _enum_value(sensor.status),
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
        "sync_mode": _enum_value(sensor.sync_mode),
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

    def add(self, scope: ProjectScope, robot: Robot, *, actor_id: UUID) -> Robot:
        scope.ensure(robot.project_id)
        _ensure_initial_version(robot.version)
        row = RobotModel(
            id=robot.robot_id,
            project_id=robot.project_id,
            created_by=actor_id,
            **_robot_values(robot, actor_id=actor_id),
        )
        self._session.add(row)
        _flush(self._session)
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
        limit: int = 20,
    ) -> list[Robot]:
        statement = _scoped_select(RobotModel, scope)
        if status is not None:
            statement = statement.where(RobotModel.status == status.value)
        statement = statement.order_by(RobotModel.created_at, RobotModel.id).limit(
            _bounded_limit(limit)
        )
        return [_robot_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        robot: Robot,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Robot:
        scope.ensure(robot.project_id)
        _ensure_expected_version(robot.version, expected_version)
        result = self._session.execute(
            update(RobotModel)
            .where(
                RobotModel.id == robot.robot_id,
                RobotModel.project_id == scope.project_id,
                RobotModel.version == expected_version,
            )
            .values(**_robot_values(robot, actor_id=actor_id), version=expected_version + 1)
        )
        if result.rowcount == 0:
            _raise_update_failure(
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

    def add(self, scope: ProjectScope, device: Device, *, actor_id: UUID) -> Device:
        scope.ensure(device.project_id)
        _ensure_initial_version(device.version)
        row = DeviceModel(
            id=device.device_id,
            project_id=device.project_id,
            created_by=actor_id,
            **_device_values(device, actor_id=actor_id),
        )
        self._session.add(row)
        _flush(self._session)
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
        limit: int = 20,
    ) -> list[Device]:
        statement = _scoped_select(DeviceModel, scope)
        if status is not None:
            statement = statement.where(DeviceModel.status == status.value)
        statement = statement.order_by(DeviceModel.created_at, DeviceModel.id).limit(
            _bounded_limit(limit)
        )
        return [_device_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        device: Device,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Device:
        scope.ensure(device.project_id)
        _ensure_expected_version(device.version, expected_version)
        result = self._session.execute(
            update(DeviceModel)
            .where(
                DeviceModel.id == device.device_id,
                DeviceModel.project_id == scope.project_id,
                DeviceModel.version == expected_version,
            )
            .values(**_device_values(device, actor_id=actor_id), version=expected_version + 1)
        )
        if result.rowcount == 0:
            _raise_update_failure(
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

    def add(self, scope: ProjectScope, sensor: Sensor, *, actor_id: UUID) -> Sensor:
        scope.ensure(sensor.project_id)
        _ensure_initial_version(sensor.version)
        row = SensorModel(
            id=sensor.sensor_id,
            project_id=sensor.project_id,
            created_by=actor_id,
            **_sensor_values(sensor, actor_id=actor_id),
        )
        self._session.add(row)
        _flush(self._session)
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
        limit: int = 20,
    ) -> list[Sensor]:
        statement = _scoped_select(SensorModel, scope)
        if status is not None:
            statement = statement.where(SensorModel.status == status.value)
        if sensor_type is not None:
            statement = statement.where(SensorModel.sensor_type == sensor_type.value)
        statement = statement.order_by(SensorModel.created_at, SensorModel.id).limit(
            _bounded_limit(limit)
        )
        return [_sensor_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        sensor: Sensor,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Sensor:
        scope.ensure(sensor.project_id)
        _ensure_expected_version(sensor.version, expected_version)
        result = self._session.execute(
            update(SensorModel)
            .where(
                SensorModel.id == sensor.sensor_id,
                SensorModel.project_id == scope.project_id,
                SensorModel.version == expected_version,
            )
            .values(**_sensor_values(sensor, actor_id=actor_id), version=expected_version + 1)
        )
        if result.rowcount == 0:
            _raise_update_failure(
                self._session,
                SensorModel,
                scope,
                sensor.sensor_id,
                "传感器",
                expected_version,
            )
        return replace(sensor, version=expected_version + 1)


def _profile_from_row(row: CalibrationProfileModel) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=row.id,
        project_id=row.project_id,
        name=row.name,
        scope=row.scope,
        target_type=row.target_type,
        version=row.version,
        schema_version=row.schema_version,
        required_types=tuple(row.required_types or ()),
        selection_rule=row.selection_rule or {},
    )


class SqlCalibrationProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        profile: CalibrationProfile,
        *,
        actor_id: UUID,
    ) -> CalibrationProfile:
        scope.ensure(profile.project_id)
        # 注意：CalibrationProfile.version 是配置版本号（DDP-101 §2.2），
        # 不是乐观锁版本，因此允许从任意正整数版本开始。
        row = CalibrationProfileModel(
            id=profile.profile_id,
            project_id=profile.project_id,
            name=profile.name,
            scope=_enum_value(profile.scope),
            target_type=_enum_value(profile.target_type),
            version=profile.version,
            schema_version=profile.schema_version,
            required_types=[_enum_value(item) for item in profile.required_types],
            selection_rule=dict(profile.selection_rule),
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(row)
        _flush(self._session)
        return profile

    def get(
        self,
        scope: ProjectScope,
        profile_id: UUID,
    ) -> CalibrationProfile | None:
        row = self._session.scalar(
            select(CalibrationProfileModel).where(
                CalibrationProfileModel.id == profile_id,
                CalibrationProfileModel.project_id == scope.project_id,
            )
        )
        return _profile_from_row(row) if row is not None else None


def _record_values(record: CalibrationRecord, *, actor_id: UUID) -> dict[str, object]:
    return {
        "profile_id": record.profile_id,
        "scope": _enum_value(record.scope),
        "target_type": _enum_value(record.target_type),
        "target_id": record.target_id,
        "calibration_type": _enum_value(record.calibration_type),
        "method": record.method,
        "parameters": dict(record.parameters),
        "parent_frame": record.parent_frame,
        "child_frame": record.child_frame,
        "valid_from": record.valid_from,
        "valid_to": record.valid_to,
        "status": _enum_value(record.status),
        "evidence_asset_ids": [str(item) for item in record.evidence_asset_ids],
        "source": record.source,
        "checksum": record.checksum,
        "uncertainty": dict(record.uncertainty),
        "performed_by": record.performed_by,
        "validated_by": record.validated_by,
        "validated_at": record.validated_at,
        "supersedes_id": record.supersedes_id,
        "updated_by": actor_id,
    }


def _record_from_row(row: CalibrationRecordModel) -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=row.id,
        project_id=row.project_id,
        profile_id=row.profile_id,
        scope=row.scope,
        target_type=row.target_type,
        target_id=row.target_id,
        calibration_type=row.calibration_type,
        method=row.method,
        parameters=row.parameters or {},
        parent_frame=row.parent_frame,
        child_frame=row.child_frame,
        valid_from=_as_utc(row.valid_from),
        valid_to=_as_utc(row.valid_to),
        status=CalibrationStatus(row.status),
        evidence_asset_ids=tuple(UUID(item) for item in (row.evidence_asset_ids or [])),
        source=row.source,
        checksum=row.checksum,
        uncertainty=row.uncertainty or {},
        performed_by=row.performed_by,
        validated_by=row.validated_by,
        validated_at=_as_utc(row.validated_at),
        supersedes_id=row.supersedes_id,
        version=row.version,
    )


class SqlCalibrationRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        actor_id: UUID,
    ) -> CalibrationRecord:
        scope.ensure(record.project_id)
        _ensure_initial_version(record.version)
        row = CalibrationRecordModel(
            id=record.calibration_id,
            project_id=record.project_id,
            created_by=actor_id,
            **_record_values(record, actor_id=actor_id),
        )
        self._session.add(row)
        _flush(self._session)
        return record

    def get(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
    ) -> CalibrationRecord | None:
        row = self._session.scalar(
            select(CalibrationRecordModel).where(
                CalibrationRecordModel.id == calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
            )
        )
        return _record_from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        target_type: str | None = None,
        target_id: UUID | None = None,
        status: CalibrationStatus | None = None,
        limit: int = 20,
    ) -> list[CalibrationRecord]:
        statement = _scoped_select(CalibrationRecordModel, scope)
        if target_type is not None:
            statement = statement.where(CalibrationRecordModel.target_type == target_type)
        if target_id is not None:
            statement = statement.where(CalibrationRecordModel.target_id == target_id)
        if status is not None:
            statement = statement.where(CalibrationRecordModel.status == status.value)
        statement = statement.order_by(
            CalibrationRecordModel.created_at,
            CalibrationRecordModel.id,
        ).limit(_bounded_limit(limit))
        return [_record_from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> CalibrationRecord:
        scope.ensure(record.project_id)
        _ensure_expected_version(record.version, expected_version)
        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == record.calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.version == expected_version,
            )
            .values(**_record_values(record, actor_id=actor_id), version=expected_version + 1)
        )
        if result.rowcount == 0:
            _raise_update_failure(
                self._session,
                CalibrationRecordModel,
                scope,
                record.calibration_id,
                "标定",
                expected_version,
            )
        return replace(record, version=expected_version + 1)

    def activate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> CalibrationRecord:
        scope.ensure(record.project_id)
        _ensure_expected_version(record.version, expected_version)
        activated = record.activate()

        # 同一事务内先替代旧 ACTIVE，再激活当前记录；部分唯一索引兜底并发。
        self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.scope == _enum_value(record.scope),
                CalibrationRecordModel.target_type == _enum_value(record.target_type),
                CalibrationRecordModel.target_id == record.target_id,
                CalibrationRecordModel.calibration_type == _enum_value(
                    record.calibration_type
                ),
                CalibrationRecordModel.status == CalibrationStatus.ACTIVE.value,
                CalibrationRecordModel.id != record.calibration_id,
            )
            .values(
                status=CalibrationStatus.SUPERSEDED.value,
                version=CalibrationRecordModel.version + 1,
                updated_by=actor_id,
            )
        )
        result = self._session.execute(
            update(CalibrationRecordModel)
            .where(
                CalibrationRecordModel.id == record.calibration_id,
                CalibrationRecordModel.project_id == scope.project_id,
                CalibrationRecordModel.version == expected_version,
                CalibrationRecordModel.status == CalibrationStatus.VALIDATED.value,
            )
            .values(**_record_values(activated, actor_id=actor_id), version=expected_version + 1)
        )
        if result.rowcount == 0:
            row = self._session.get(CalibrationRecordModel, record.calibration_id)
            if row is None or row.project_id != scope.project_id:
                raise ResourceNotFoundError("标定", record.calibration_id)
            if row.version != expected_version:
                raise VersionConflictError("标定", expected_version)
            raise InvalidStateTransitionError("标定", row.status, CalibrationStatus.ACTIVE.value)
        return replace(activated, version=expected_version + 1)
