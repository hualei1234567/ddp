"""机器人用例：项目作用域查询、档案写入、生命周期动作与标定配置登记。

复用说明（CLAUDE.md §一）：本模块不新建领域模型或仓储——
``Robot`` 与 ``SqlRobotRepository`` 早已存在并有测试，此前仅因缺少路由而
无法对外暴露（G-01）。这里补的是用例编排，规则仍落在 Domain。

与 Device 的差异：Robot 的 ``calibration_profile_id`` 必须指向
``target_type=ROBOT`` 的 Profile（Device 对应 DEVICE）。这类「实体类型 ↔ Profile
目标类型」的对应关系只在两处出现，各自内联在用例里；若将来出现第三种，
再提取公共校验函数。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.application.audit import record_change
from app.application.devices.calibration_gate import ensure_required_calibrations_active
from app.application.devices.commands import NewRobot
from app.application.devices.idempotency import IdempotencyGuard, validate_key
from app.application.devices.pagination import Page, build_page, decode_cursor, normalize_limit
from app.application.devices.ports import (
    AuditRepository,
    CalibrationProfileRepository,
    CalibrationRecordRepository,
    IdempotencyRepository,
    OutboxRepository,
    RobotRepository,
    UnitOfWork,
)
from app.domain.common.ids import new_id
from app.domain.devices.enums import CalibrationTargetType, LifecycleStatus
from app.domain.devices.errors import (
    CalibrationProfileInvalidError,
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.devices.models import Robot
from app.domain.identity.models import ProjectScope

_PATCHABLE_FIELDS = frozenset(
    {
        "code",
        "name",
        "robot_type",
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

_ACTIONS: dict[str, tuple[str, Callable[[Robot], Robot]]] = {
    "activate": ("激活", lambda item: item.activate()),
    "enter-maintenance": ("维护", lambda item: item.enter_maintenance()),
    "report-fault": ("故障", lambda item: item.report_fault()),
    "suspend": ("挂起", lambda item: item.suspend()),
    "retire": ("退役", lambda item: item.retire()),
}

# R-13：事件类型用字面量映射表，禁止 f-string——AST 提取脚本抓不到 f-string，
# 新增事件会静默逃过 ``scripts/check_outbox_events.py`` 的归类校验。
ROBOT_ACTION_EVENTS: dict[str, str] = {
    "activate": "robot.activate",
    "enter-maintenance": "robot.enter-maintenance",
    "report-fault": "robot.report-fault",
    "suspend": "robot.suspend",
    "retire": "robot.retire",
}


class RobotUseCases:
    def __init__(
        self,
        robots: RobotRepository,
        profiles: CalibrationProfileRepository,
        calibrations: CalibrationRecordRepository,
        uow: UnitOfWork,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
    ) -> None:
        self._robots = robots
        self._profiles = profiles
        self._calibrations = calibrations
        self._uow = uow
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)

    def list_robots(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[Robot]:
        bounded = normalize_limit(limit)
        items = self._robots.list(
            scope,
            status=_lifecycle_status(status),
            after_id=decode_cursor(cursor),
            limit=bounded + 1,
        )
        return build_page(items, limit=bounded, id_of=lambda item: item.robot_id)

    def get_robot(self, scope: ProjectScope, robot_id: UUID) -> Robot:
        robot = self._robots.get(scope, robot_id)
        if robot is None:
            raise ResourceNotFoundError("机器人", robot_id)
        return robot

    def create_robot(
        self,
        scope: ProjectScope,
        command: NewRobot,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> Robot:
        key = validate_key(idempotency_key)
        body = {
            "code": command.code,
            "name": command.name,
            "robot_type": command.robot_type,
            "serial_number": command.serial_number,
            "calibration_profile_id": (
                str(command.calibration_profile_id) if command.calibration_profile_id else None
            ),
        }
        now = datetime.now(UTC)
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self._replay(scope, replay.response_ref)
        self._validate_profile(scope, command.calibration_profile_id)
        robot = Robot(
            robot_id=new_id(),
            project_id=scope.project_id,
            code=command.code,
            name=command.name,
            robot_type=command.robot_type,
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
        created = self._robots.add(scope, robot)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="robot",
            resource_id=created.robot_id,
            action="create",
            from_status=None,
            to_status=_enum_value(created.status),
            reason=None,
            request_id=request_id,
            details={"code": created.code, "robot_type": created.robot_type},
            event_type="robot.created",
            event_payload={
                "robot_id": str(created.robot_id),
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
            response_ref=str(created.robot_id),
            now=now,
        )
        self._uow.commit()
        return created

    def update_robot(
        self,
        scope: ProjectScope,
        robot_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> Robot:
        if set(changes) - _PATCHABLE_FIELDS:
            raise DomainValidationError("请求包含不允许修改的字段")
        # 契约允许 capability_profile 为 null，语义是"本次不改"。
        # 不能把 None 传给领域层：frozen_mapping 只接受 Mapping，会 422。
        # 也不能把它当空对象：那会静默清空已有的能力声明。
        changes = {
            key: value
            for key, value in changes.items()
            if not (key == "capability_profile" and value is None)
        }
        current = self.get_robot(scope, robot_id)
        if current.version != expected_version:
            raise VersionConflictError("机器人", expected_version)
        if "calibration_profile_id" in changes:
            self._validate_profile(
                scope, _optional_uuid(changes["calibration_profile_id"], "calibration_profile_id")
            )
        updated = self._robots.save(
            scope,
            replace(current, **dict(changes)),
            expected_version=expected_version,
        )
        self._uow.commit()
        return updated

    def apply_action(
        self,
        scope: ProjectScope,
        robot_id: UUID,
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
    ) -> Robot:
        key = validate_key(idempotency_key)
        entry = _ACTIONS.get(action)
        if entry is None:
            raise DomainValidationError("未知机器人动作")
        body = {
            "robot_id": str(robot_id),
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
            return self._replay(scope, replay.response_ref)
        current = self.get_robot(scope, robot_id)
        if current.version != expected_version:
            raise VersionConflictError("机器人", expected_version)
        if action == "report-fault" and not (fault_code or "").strip():
            raise DomainValidationError("report-fault 必须提供 fault_code")
        if action == "activate":
            # G-20：与 Device/Sensor 共用同一门禁实现（规则只有一处）。
            ensure_required_calibrations_active(
                calibrations=self._calibrations,
                profiles=self._profiles,
                scope=scope,
                profile_id=current.calibration_profile_id,
                target_type=CalibrationTargetType.ROBOT,
                target_id=current.robot_id,
            )
        target = entry[1](current)
        updated = self._robots.save(
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
            resource_type="robot",
            resource_id=updated.robot_id,
            action=action,
            from_status=_enum_value(current.status),
            to_status=_enum_value(updated.status),
            reason=reason,
            request_id=request_id,
            details={
                "fault_code": fault_code,
                "fault_detail": fault_detail,
                "fault_occurred_at": (
                    fault_occurred_at.isoformat() if fault_occurred_at is not None else None
                ),
            },
            event_type=ROBOT_ACTION_EVENTS[action],
            event_payload={
                "robot_id": str(updated.robot_id),
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
            response_ref=str(updated.robot_id),
            now=now,
        )
        self._uow.commit()
        return updated

    def _replay(self, scope: ProjectScope, response_ref: str | None) -> Robot:
        if not response_ref:
            raise ResourceNotFoundError("机器人", response_ref)
        try:
            robot_id = UUID(response_ref)
        except ValueError as exc:
            raise ResourceNotFoundError("机器人", response_ref) from exc
        return self.get_robot(scope, robot_id)

    def _validate_profile(self, scope: ProjectScope, profile_id: UUID | None) -> None:
        if profile_id is None:
            return
        profile = self._profiles.get(scope, profile_id)
        if profile is None:
            raise CalibrationProfileInvalidError("标定配置不存在或不属于当前项目")
        if profile.target_type is not CalibrationTargetType.ROBOT:
            raise CalibrationProfileInvalidError("标定配置 target_type 必须为 ROBOT")


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
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是 UUID") from exc


def _enum_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = ["RobotUseCases"]
