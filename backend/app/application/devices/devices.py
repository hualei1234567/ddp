"""设备聚合用例：项目作用域查询、档案写入与生命周期动作。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import record_change
from app.application.devices.commands import NewDevice
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
    DeviceRepository,
    IdempotencyRepository,
    OutboxRepository,
    RobotRepository,
    UnitOfWork,
)
from app.domain.common.ids import new_id
from app.domain.devices.enums import (
    CalibrationTargetType,
    DeviceType,
    LifecycleStatus,
)
from app.domain.devices.errors import (
    CalibrationProfileInvalidError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.devices.models import Device
from app.domain.identity.models import ProjectScope

_PATCHABLE_FIELDS = frozenset(
    {
        "code",
        "name",
        "device_type",
        "robot_id",
        "vendor",
        "model",
        "serial_number",
        "firmware_version",
        "hardware_version",
        "calibration_profile_id",
        "capability_profile",
        "location_id",
        "owner_user_id",
    }
)

_ACTIONS: dict[str, tuple[str, Callable[[Device], Device]]] = {
    "activate": ("激活", lambda item: item.activate()),
    "enter-maintenance": ("维护", lambda item: item.enter_maintenance()),
    "report-fault": ("故障", lambda item: item.report_fault()),
    "suspend": ("挂起", lambda item: item.suspend()),
    "retire": ("退役", lambda item: item.retire()),
}


class DeviceUseCases:
    def __init__(
        self,
        devices: DeviceRepository,
        robots: RobotRepository,
        profiles: CalibrationProfileRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._devices = devices
        self._robots = robots
        self._profiles = profiles
        self._uow = uow
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)

    def list_devices(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[Device]:
        bounded_limit = normalize_limit(limit)
        status_filter = _lifecycle_status(status)
        items = self._devices.list(
            scope,
            status=status_filter,
            after_id=decode_cursor(cursor),
            limit=bounded_limit + 1,
        )
        return build_page(items, limit=bounded_limit, id_of=lambda item: item.device_id)

    def _replay_device(self, scope: ProjectScope, response_ref: str | None) -> Device:
        """按幂等记录重放：只返回当前项目内的资源，越权与缺失同样 404。"""
        if not response_ref:
            raise ResourceNotFoundError("设备", response_ref)
        try:
            device_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("设备", response_ref) from exc
        return self.get_device(scope, device_id)

    def get_device(self, scope: ProjectScope, device_id: UUID) -> Device:
        device = self._devices.get(scope, device_id)
        if device is None:
            raise ResourceNotFoundError("设备", device_id)
        return device

    def create_device(
        self,
        scope: ProjectScope,
        command: NewDevice,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Device:
        key = validate_key(idempotency_key)
        body = {
            "code": command.code,
            "name": command.name,
            "device_type": command.device_type,
            "robot_id": str(command.robot_id) if command.robot_id else None,
            "serial_number": command.serial_number,
            "calibration_profile_id": (
                str(command.calibration_profile_id) if command.calibration_profile_id else None
            ),
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_device(scope, replay.response_ref)
        self._validate_robot(scope, command.robot_id)
        self._validate_profile(scope, command.calibration_profile_id)
        device = Device(
            device_id=new_id(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            device_type=command.device_type,
            robot_id=command.robot_id,
            vendor=command.vendor,
            model=command.model,
            serial_number=command.serial_number,
            firmware_version=command.firmware_version,
            hardware_version=command.hardware_version,
            calibration_profile_id=command.calibration_profile_id,
            capability_profile=command.capability_profile,
            location_id=command.location_id,
            owner_user_id=command.owner_user_id,
        )
        created = self._devices.add(scope, device)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="device",
            resource_id=created.device_id,
            action="create",
            from_status=None,
            to_status=_enum_value(created.status),
            reason=None,
            request_id=request_id,
            details={"code": created.code, "device_type": _enum_value(created.device_type)},
            event_type="device.created",
            event_payload={
                "device_id": str(created.device_id),
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
            response_ref=str(created.device_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_device(
        self,
        scope: ProjectScope,
        device_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> Device:
        self._ensure_patchable(changes)
        current = self.get_device(scope, device_id)
        if current.version != expected_version:
            raise VersionConflictError("设备", expected_version)
        if "robot_id" in changes:
            self._validate_robot(scope, _optional_uuid(changes["robot_id"], "robot_id"))
        if "calibration_profile_id" in changes:
            self._validate_profile(
                scope,
                _optional_uuid(changes["calibration_profile_id"], "calibration_profile_id"),
            )
        updated = self._devices.save(
            scope,
            _replace_device(current, changes),
            expected_version=expected_version,
        )
        self._uow.commit()
        return updated

    def apply_action(
        self,
        scope: ProjectScope,
        device_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        fault_code: str | None = None,
        fault_detail: str | None = None,
        fault_occurred_at: datetime | None = None,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Device:
        """生命周期动作；请求体的语义字段必须落库并可追溯（G-10/G-11）。"""
        key = validate_key(idempotency_key)
        entry = _ACTIONS.get(action)
        if entry is None:
            raise DomainValidationError("未知设备动作")
        body = {
            "device_id": str(device_id),
            "action": action,
            "expected_version": expected_version,
            "reason": reason,
            "fault_code": fault_code,
            "fault_occurred_at": (
                fault_occurred_at.isoformat() if fault_occurred_at is not None else None
            ),
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay_device(scope, replay.response_ref)

        current = self.get_device(scope, device_id)
        if current.version != expected_version:
            raise VersionConflictError("设备", expected_version)
        if action == "report-fault" and not (fault_code or "").strip():
            raise DomainValidationError("report-fault 必须提供 fault_code")

        target = entry[1](current)
        updated = self._devices.save(
            scope,
            replace(
                target,
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
            resource_type="device",
            resource_id=updated.device_id,
            action=action,
            from_status=_enum_value(current.status),
            to_status=_enum_value(updated.status),
            reason=reason,
            request_id=request_id,
            details={
                "fault_code": fault_code,
                "fault_detail": fault_detail,
                "fault_occurred_at": (fault_occurred_at.isoformat() if fault_occurred_at else None),
            },
            event_type=f"device.{action}",
            event_payload={
                "device_id": str(updated.device_id),
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
            response_ref=str(updated.device_id),
            now=now,
        )
        self._uow.commit()
        return updated

    def _ensure_patchable(self, changes: Mapping[str, object]) -> None:
        if set(changes) - _PATCHABLE_FIELDS:
            raise DomainValidationError("请求包含不允许修改的字段")

    def _validate_robot(self, scope: ProjectScope, robot_id: UUID | None) -> None:
        if robot_id is None:
            return
        if self._robots.get(scope, robot_id) is None:
            raise ResourceNotFoundError("机器人", robot_id)

    def _validate_profile(
        self,
        scope: ProjectScope,
        profile_id: UUID | None,
    ) -> None:
        if profile_id is None:
            return
        profile = self._profiles.get(scope, profile_id)
        if profile is None:
            raise CalibrationProfileInvalidError("标定配置不存在或不属于当前项目")
        if profile.target_type is not CalibrationTargetType.DEVICE:
            raise CalibrationProfileInvalidError("标定配置 target_type 必须为 DEVICE")


def _lifecycle_status(value: str | None) -> LifecycleStatus | None:
    if value is None:
        return None
    try:
        return LifecycleStatus(value)
    except ValueError as exc:
        raise DomainValidationError("status 取值非法") from exc


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def _replace_device(current: Device, changes: Mapping[str, object]) -> Device:
    if "device_type" in changes and not isinstance(changes["device_type"], DeviceType | str):
        raise DomainValidationError("device_type 必须是字符串")
    return replace(current, **dict(changes))


def _enum_value(value: object) -> str:
    """枚举或字符串统一取字符串值；只用于审计与事件负载。"""
    return value.value if hasattr(value, "value") else str(value)
