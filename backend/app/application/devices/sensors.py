"""传感器聚合用例：项目作用域查询、档案写入与生命周期动作。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import record_change
from app.application.devices.action_preconditions import (
    assert_action_payload_complete,
)
from app.application.devices.calibration_gate import ensure_required_calibrations_active
from app.application.devices.commands import NewSensor
from app.application.devices.idempotency import IdempotencyGuard, validate_key
from app.application.devices.pagination import (
    Page,
    build_page,
    decode_cursor,
    normalize_limit,
)
from app.application.devices.ports import (
    AuditRepository,
    CalibrationProfileRepository,
    CalibrationRecordRepository,
    DeviceRepository,
    IdempotencyRepository,
    OutboxRepository,
    RobotRepository,
    SensorRepository,
    UnitOfWork,
)
from app.domain.common.ids import new_id
from app.domain.devices.enums import (
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    LifecycleStatus,
    SensorType,
)
from app.domain.devices.errors import (
    CalibrationProfileInvalidError,
    CalibrationTargetInvalidError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.devices.models import Sensor
from app.domain.identity.models import ProjectScope

_PATCHABLE_FIELDS = frozenset(
    {
        "code",
        "sensor_type",
        "robot_id",
        "device_id",
        "vendor",
        "model",
        "serial_number",
        "sample_rate_hz",
        "resolution_w",
        "resolution_h",
        "bit_depth",
        "channels",
        "intrinsic_calibration_id",
        "extrinsic_calibration_id",
        "coordinate_frame",
        # G-08：ADR-0019 的新坐标系字段此前漏在创建与 PATCH 两处，
        # 导致 POST 传 frame_convention 落库成 UNSPECIFIED、PATCH 直接被 422 拒掉。
        "frame_id",
        "frame_convention",
        "sync_mode",
        "sync_group",
        "firmware_version",
        "calibration_profile_id",
        "mount_position",
    }
)

_ACTIONS: dict[str, Callable[[Sensor], Sensor]] = {
    "activate": lambda item: item.activate(),
    "enter-maintenance": lambda item: item.enter_maintenance(),
    "report-fault": lambda item: item.report_fault(),
    "suspend": lambda item: item.suspend(),
    "retire": lambda item: item.retire(),
}

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
SENSOR_ACTION_EVENTS: dict[str, str] = {
    "activate": "sensor.activate",
    "enter-maintenance": "sensor.enter-maintenance",
    "report-fault": "sensor.report-fault",
    "suspend": "sensor.suspend",
    "retire": "sensor.retire",
}


class SensorUseCases:
    def __init__(
        self,
        sensors: SensorRepository,
        devices: DeviceRepository,
        robots: RobotRepository,
        profiles: CalibrationProfileRepository,
        calibrations: CalibrationRecordRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._sensors = sensors
        self._devices = devices
        self._robots = robots
        self._profiles = profiles
        self._calibrations = calibrations
        self._uow = uow
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)

    def list_sensors(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        sensor_type: str | None = None,
        robot_id: UUID | None = None,
        device_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[Sensor]:
        bounded_limit = normalize_limit(limit)
        items = self._sensors.list(
            scope,
            status=_lifecycle_status(status),
            sensor_type=_sensor_type(sensor_type),
            robot_id=robot_id,
            device_id=device_id,
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        return build_page(items, limit=bounded_limit, id_of=lambda item: item.sensor_id)

    def _replay_sensor(self, scope: ProjectScope, response_ref: str | None) -> Sensor:
        """按幂等记录重放：只返回当前项目内的资源，越权与缺失同样 404。"""
        if not response_ref:
            raise ResourceNotFoundError("传感器", response_ref)
        try:
            sensor_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("传感器", response_ref) from exc
        return self.get_sensor(scope, sensor_id)

    def get_sensor(self, scope: ProjectScope, sensor_id: UUID) -> Sensor:
        sensor = self._sensors.get(scope, sensor_id)
        if sensor is None:
            raise ResourceNotFoundError("传感器", sensor_id)
        return sensor

    def create_sensor(
        self,
        scope: ProjectScope,
        command: NewSensor,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Sensor:
        key = validate_key(idempotency_key)
        body = {
            "code": command.code,
            "sensor_type": command.sensor_type,
            "sample_rate_hz": command.sample_rate_hz,
            "coordinate_frame": command.coordinate_frame,
            "device_id": str(command.device_id) if command.device_id else None,
            "robot_id": str(command.robot_id) if command.robot_id else None,
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_sensor(scope, replay.response_ref)
        sensor = Sensor(
            sensor_id=new_id(),
            project_id=scope.project_id,
            code=command.code,
            sensor_type=command.sensor_type,
            sample_rate_hz=command.sample_rate_hz,
            coordinate_frame=command.coordinate_frame,
            frame_id=command.frame_id,
            frame_convention=command.frame_convention,
            robot_id=command.robot_id,
            device_id=command.device_id,
            vendor=command.vendor,
            model=command.model,
            serial_number=command.serial_number,
            resolution_w=command.resolution_w,
            resolution_h=command.resolution_h,
            bit_depth=command.bit_depth,
            channels=command.channels,
            intrinsic_calibration_id=command.intrinsic_calibration_id,
            extrinsic_calibration_id=command.extrinsic_calibration_id,
            sync_mode=command.sync_mode,
            sync_group=command.sync_group,
            firmware_version=command.firmware_version,
            calibration_profile_id=command.calibration_profile_id,
            mount_position=command.mount_position,
        )
        self._validate_references(scope, sensor)
        created = self._sensors.add(scope, sensor)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="sensor",
            resource_id=created.sensor_id,
            action="create",
            from_status=None,
            to_status=_enum_value(created.status),
            reason=None,
            request_id=request_id,
            details={"code": created.code, "sensor_type": _enum_value(created.sensor_type)},
            event_type="sensor.created",
            event_payload={
                "sensor_id": str(created.sensor_id),
                "code": created.code,
                "status": _enum_value(created.status),
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.sensor_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_sensor(
        self,
        scope: ProjectScope,
        sensor_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> Sensor:
        _ensure_patchable(changes)
        current = self.get_sensor(scope, sensor_id)
        if current.version != expected_version:
            raise VersionConflictError("传感器", expected_version)
        candidate = replace(current, **dict(changes))
        self._validate_references(scope, candidate)
        updated = self._sensors.save(
            scope,
            candidate,
            expected_version=expected_version,
        )
        self._uow.commit()
        return updated

    def apply_action(
        self,
        scope: ProjectScope,
        sensor_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        fault_code: str | None = None,
        fault_detail: str | None = None,
        fault_occurred_at: datetime | None = None,
        maintenance_record_id: UUID | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Sensor:
        """生命周期动作；请求体的语义字段必须落库并可追溯（G-10/G-11）。"""
        key = validate_key(idempotency_key)
        handler = _ACTIONS.get(action)
        if handler is None:
            raise DomainValidationError("未知传感器动作")
        body = {
            "sensor_id": str(sensor_id),
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
            "fault_code": fault_code,
            "maintenance_record_id": (
                str(maintenance_record_id) if maintenance_record_id is not None else None
            ),
            "fault_occurred_at": (
                fault_occurred_at.isoformat() if fault_occurred_at is not None else None
            ),
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_sensor(scope, replay.response_ref)

        current = self.get_sensor(scope, sensor_id)
        if current.version != expected_version:
            raise VersionConflictError("传感器", expected_version)
        # 契约 §8.1：动作的必填字段（reason / fault_code / occurred_at /
        # maintenance_record_id）由 action_preconditions 统一校验，规则只一处。
        assert_action_payload_complete(
            action,
            reason=reason,
            fault_code=fault_code,
            occurred_at=fault_occurred_at,
            maintenance_record_id=maintenance_record_id,
        )
        if action == "report-fault" and fault_occurred_at is None:
            # 客户端未给上报时刻时用服务端当前时间：跳过这个字段不该让
            # fault_occurred_at 落成 NULL——那样故障时间线就断了。
            fault_occurred_at = now
        if action == "activate":
            # G-20：与 Device/Robot 共用同一门禁实现（规则只有一处）。
            ensure_required_calibrations_active(
                calibrations=self._calibrations,
                profiles=self._profiles,
                scope=scope,
                profile_id=current.calibration_profile_id,
                target_type=CalibrationTargetType.SENSOR,
                target_id=current.sensor_id,
            )

        updated = self._sensors.save(
            scope,
            replace(
                handler(current),
                status_reason=reason,
                fault_code=fault_code,
                fault_detail=fault_detail,
                fault_occurred_at=fault_occurred_at,
                status_changed_at=now,
                status_changed_by=scope.actor_id,
            ),
            expected_version=expected_version,
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="sensor",
            resource_id=updated.sensor_id,
            action=action,
            from_status=_enum_value(current.status),
            to_status=_enum_value(updated.status),
            reason=reason,
            request_id=request_id,
            details={
                "fault_code": fault_code,
                "fault_detail": fault_detail,
                "fault_occurred_at": (fault_occurred_at.isoformat() if fault_occurred_at else None),
                # 契约 §8.1 把维修工单号列为必填留痕项：事后要能对应到工单系统。
                "maintenance_record_id": (
                    str(maintenance_record_id) if maintenance_record_id is not None else None
                ),
            },
            event_type=SENSOR_ACTION_EVENTS[action],
            event_payload={
                "sensor_id": str(updated.sensor_id),
                "from_status": _enum_value(current.status),
                "to_status": _enum_value(updated.status),
                "action": action,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(updated.sensor_id),
            now=now,
        )
        self._uow.commit()
        return updated

    def _validate_references(self, scope: ProjectScope, sensor: Sensor) -> None:
        if sensor.robot_id is not None and self._robots.get(scope, sensor.robot_id) is None:
            raise ResourceNotFoundError("机器人", sensor.robot_id)
        if sensor.device_id is not None and self._devices.get(scope, sensor.device_id) is None:
            raise ResourceNotFoundError("设备", sensor.device_id)
        if sensor.calibration_profile_id is not None:
            profile = self._profiles.get(scope, sensor.calibration_profile_id)
            if profile is None:
                raise CalibrationProfileInvalidError("标定配置不存在或不属于当前项目")
            if profile.target_type is not CalibrationTargetType.SENSOR:
                raise CalibrationProfileInvalidError("标定配置 target_type 必须为 SENSOR")
        self._validate_calibration(
            scope,
            sensor.intrinsic_calibration_id,
            sensor.sensor_id,
            CalibrationType.INTRINSICS,
        )
        self._validate_calibration(
            scope,
            sensor.extrinsic_calibration_id,
            sensor.sensor_id,
            CalibrationType.EXTRINSICS,
        )

    def _validate_calibration(
        self,
        scope: ProjectScope,
        calibration_id: UUID | None,
        sensor_id: UUID,
        expected_type: CalibrationType,
    ) -> None:
        if calibration_id is None:
            return
        record = self._calibrations.get(scope, calibration_id)
        if record is None:
            raise CalibrationTargetInvalidError("标定记录不存在或不属于当前项目")
        if (
            record.target_type is not CalibrationTargetType.SENSOR
            or record.target_id != sensor_id
            or record.calibration_type is not expected_type
        ):
            raise CalibrationTargetInvalidError("标定记录目标或类型与传感器不匹配")
        if record.status not in {CalibrationStatus.VALIDATED, CalibrationStatus.ACTIVE}:
            raise CalibrationTargetInvalidError("传感器只能引用 VALIDATED/ACTIVE 标定")


def _ensure_patchable(changes: Mapping[str, object]) -> None:
    if set(changes) - _PATCHABLE_FIELDS:
        raise DomainValidationError("请求包含不允许修改的字段")


def _lifecycle_status(value: str | None) -> LifecycleStatus | None:
    if value is None:
        return None
    try:
        return LifecycleStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _sensor_type(value: str | None) -> SensorType | None:
    if value is None:
        return None
    try:
        return SensorType(value)
    except ValueError as exc:
        raise DomainValidationError("sensor_type 取值非法") from exc


def _enum_value(value: object) -> str:
    """枚举或字符串统一取字符串值；只用于审计与事件负载。"""
    return value.value if hasattr(value, "value") else str(value)
