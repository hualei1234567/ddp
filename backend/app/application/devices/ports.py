"""设备域仓储端口。

所有方法都要求传入服务端构造的 :class:`ProjectScope`；
按 ID 查询必须带项目过滤，跨项目资源返回 ``None``，
由应用层统一映射为 ``resource_not_found``，避免 IDOR 枚举。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationStatus,
    LifecycleStatus,
    SensorType,
)
from app.domain.devices.models import Device, Robot, Sensor
from app.domain.identity.models import ProjectScope


class RobotRepository(Protocol):
    def add(self, scope: ProjectScope, robot: Robot, *, actor_id: UUID) -> Robot: ...

    def get(self, scope: ProjectScope, robot_id: UUID) -> Robot | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Robot | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        limit: int = 20,
    ) -> list[Robot]: ...

    def save(
        self,
        scope: ProjectScope,
        robot: Robot,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Robot: ...


class DeviceRepository(Protocol):
    def add(self, scope: ProjectScope, device: Device, *, actor_id: UUID) -> Device: ...

    def get(self, scope: ProjectScope, device_id: UUID) -> Device | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Device | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        limit: int = 20,
    ) -> list[Device]: ...

    def save(
        self,
        scope: ProjectScope,
        device: Device,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Device: ...


class SensorRepository(Protocol):
    def add(self, scope: ProjectScope, sensor: Sensor, *, actor_id: UUID) -> Sensor: ...

    def get(self, scope: ProjectScope, sensor_id: UUID) -> Sensor | None: ...

    def get_by_code(self, scope: ProjectScope, code: str) -> Sensor | None: ...

    def list(
        self,
        scope: ProjectScope,
        *,
        status: LifecycleStatus | None = None,
        sensor_type: SensorType | None = None,
        limit: int = 20,
    ) -> list[Sensor]: ...

    def save(
        self,
        scope: ProjectScope,
        sensor: Sensor,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> Sensor: ...


class CalibrationProfileRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        profile: CalibrationProfile,
        *,
        actor_id: UUID,
    ) -> CalibrationProfile: ...

    def get(self, scope: ProjectScope, profile_id: UUID) -> CalibrationProfile | None: ...


class CalibrationRecordRepository(Protocol):
    def add(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        actor_id: UUID,
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
        limit: int = 20,
    ) -> list[CalibrationRecord]: ...

    def save(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> CalibrationRecord: ...

    def activate(
        self,
        scope: ProjectScope,
        record: CalibrationRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> CalibrationRecord:
        """激活已校验记录，并原子地把同目标旧 ACTIVE 记录转为 SUPERSEDED。"""
