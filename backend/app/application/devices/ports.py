"""设备域仓储端口。

所有方法都要求传入服务端构造的 :class:`ProjectScope`；
按 ID 查询必须带项目过滤，跨项目资源返回 ``None``，
由应用层统一映射为 ``resource_not_found``，避免 IDOR 枚举。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.application.audit import AuditRecord as _AuditRecord
from app.application.audit import AuditRepository as _AuditRepository
from app.application.audit import OutboxEvent as _OutboxEvent
from app.application.audit import OutboxRepository as _OutboxRepository
from app.application.idempotency import IdempotencyRecord as _IdempotencyRecord
from app.application.idempotency import IdempotencyRepository as _IdempotencyRepository
from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationStatus,
    LifecycleStatus,
    SensorType,
)
from app.domain.devices.models import Device, Robot, Sensor
from app.domain.identity.models import ProjectScope


class UnitOfWork(Protocol):
    """事务边界端口；由 infrastructure 的 ``Session`` 适配。"""

    def commit(self) -> None: ...


class RobotRepository(Protocol):
    def add(self, scope: ProjectScope, robot: Robot) -> Robot: ...

    def get(self, scope: ProjectScope, robot_id: UUID) -> Robot | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Robot | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Robot]: ...

    def save(
        self,
        scope: ProjectScope,
        robot: Robot,
        *,
        expected_version: int,
    ) -> Robot: ...


class DeviceRepository(Protocol):
    def add(self, scope: ProjectScope, device: Device) -> Device: ...

    def get(self, scope: ProjectScope, device_id: UUID) -> Device | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Device | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[Device]: ...

    def save(
        self,
        scope: ProjectScope,
        device: Device,
        *,
        expected_version: int,
    ) -> Device: ...


class SensorRepository(Protocol):
    def add(self, scope: ProjectScope, sensor: Sensor) -> Sensor: ...

    def get(self, scope: ProjectScope, sensor_id: UUID) -> Sensor | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Sensor | None: ...

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
    ) -> list[Sensor]: ...

    def save(
        self,
        scope: ProjectScope,
        sensor: Sensor,
        *,
        expected_version: int,
    ) -> Sensor: ...


class CalibrationProfileRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        profile: CalibrationProfile,
    ) -> CalibrationProfile: ...

    def get(self, scope: ProjectScope, profile_id: UUID) -> CalibrationProfile | None: ...

    def find_successor(
        self,
        scope: ProjectScope,
        *,
        previous_id: UUID,
    ) -> CalibrationProfile | None:
        """返回 ``supersedes_id == previous_id`` 的那一版（用于拦截版本链分叉）。"""
        ...


class CalibrationRecordRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
    ) -> CalibrationRecord: ...

    def get(
        self,
        scope: ProjectScope,
        calibration_id: UUID,
    ) -> CalibrationRecord | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        target_type: str | None = None,
        target_id: UUID | None = None,
        status: CalibrationStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[CalibrationRecord]: ...

    def save(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord:
        """仅编辑草稿白名单字段；绝不写 status。"""

    def submit(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def validate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        validator_id: UUID,
        validated_at: datetime,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def reject(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def reopen(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def revoke(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def expire(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
    ) -> CalibrationRecord: ...

    def activate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> CalibrationRecord:
        """校验状态/版本/有效期后原子激活，并替代同目标旧 ACTIVE。"""


# 审计、outbox 与幂等记录类型统一由 application 顶层声明，本域直接复用。
AuditRecord = _AuditRecord
OutboxEvent = _OutboxEvent
AuditRepository = _AuditRepository
OutboxRepository = _OutboxRepository
IdempotencyRecord = _IdempotencyRecord
IdempotencyRepository = _IdempotencyRepository
