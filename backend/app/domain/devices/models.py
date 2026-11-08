"""机器人、设备、传感器领域实体。

实体为不可变 dataclass；状态只能通过 `transition_to` 迁移，
迁移失败抛 :class:`InvalidStateTransitionError`，不存在通用状态字段写入入口。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import ClassVar, Self
from uuid import UUID

from app.domain.devices.enums import (
    DeviceType,
    LifecycleStatus,
    SensorSyncMode,
    SensorType,
)
from app.domain.devices.errors import DomainValidationError
from app.domain.devices.state_machines import assert_lifecycle_transition
from app.domain.devices.validation import (
    CODE_MAX_LENGTH,
    FRAME_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    NAME_MAX_LENGTH,
    VERSION_MAX_LENGTH,
    coerce_enum,
    frozen_mapping,
    normalized_text,
    optional_text,
    positive_float,
    positive_int,
)


class _LifecycleActions:
    """机器人/设备/传感器共用的具名状态动作。

    只暴露契约允许的动作；“任意非终态→SUSPENDED/RETIRED”等前置条件
    仍由具体用例校验（如无在途任务），领域层只保证状态机合法。
    """

    __slots__ = ()

    _domain_label: ClassVar[str] = "资源"

    def transition_to(self, target: LifecycleStatus | str) -> Self:
        target_status = coerce_enum(target, LifecycleStatus, "target_status")
        assert_lifecycle_transition(self._domain_label, self.status, target_status)
        return replace(self, status=target_status)

    def activate(self) -> Self:
        return self.transition_to(LifecycleStatus.ACTIVE)

    def enter_maintenance(self) -> Self:
        return self.transition_to(LifecycleStatus.IN_MAINTENANCE)

    def report_fault(self) -> Self:
        return self.transition_to(LifecycleStatus.FAULT)

    def suspend(self) -> Self:
        return self.transition_to(LifecycleStatus.SUSPENDED)

    def retire(self) -> Self:
        return self.transition_to(LifecycleStatus.RETIRED)


@dataclass(frozen=True, slots=True)
class Robot(_LifecycleActions):
    """机器人聚合根。"""

    _domain_label: ClassVar[str] = "机器人"

    robot_id: UUID
    project_id: UUID
    code: str
    name: str
    robot_type: str
    status: LifecycleStatus | str = LifecycleStatus.PROVISIONED
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    calibration_profile_id: UUID | None = None
    capability_profile: Mapping[str, object] = field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "robot_type",
            normalized_text(
                self.robot_type,
                "robot_type",
                max_length=CODE_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, LifecycleStatus, "status"),
        )
        for name in ("vendor", "model", "serial_number"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=LABEL_MAX_LENGTH),
            )
        for name in ("firmware_version", "hardware_version"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=VERSION_MAX_LENGTH),
            )
        object.__setattr__(
            self,
            "capability_profile",
            frozen_mapping(self.capability_profile, "capability_profile"),
        )
        if self.version < 1:
            raise DomainValidationError("version 必须大于等于 1")


@dataclass(frozen=True, slots=True)
class Device(_LifecycleActions):
    """设备聚合根；设备可独立存在，也可挂载到机器人。"""

    _domain_label: ClassVar[str] = "设备"

    device_id: UUID
    project_id: UUID
    code: str
    name: str
    device_type: DeviceType | str
    robot_id: UUID | None = None
    status: LifecycleStatus | str = LifecycleStatus.PROVISIONED
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    calibration_profile_id: UUID | None = None
    capability_profile: Mapping[str, object] = field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            normalized_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "device_type",
            coerce_enum(self.device_type, DeviceType, "device_type"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, LifecycleStatus, "status"),
        )
        for name in ("vendor", "model", "serial_number"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=LABEL_MAX_LENGTH),
            )
        for name in ("firmware_version", "hardware_version"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=VERSION_MAX_LENGTH),
            )
        object.__setattr__(
            self,
            "capability_profile",
            frozen_mapping(self.capability_profile, "capability_profile"),
        )
        if self.version < 1:
            raise DomainValidationError("version 必须大于等于 1")


@dataclass(frozen=True, slots=True)
class Sensor(_LifecycleActions):
    """传感器聚合根。

    ``robot_id`` 与 ``device_id`` 至少提供一个；二者可同时提供，
    表示传感器安装在挂载于机器人的设备上。
    """

    _domain_label: ClassVar[str] = "传感器"

    sensor_id: UUID
    project_id: UUID
    code: str
    sensor_type: SensorType | str
    sample_rate_hz: float
    coordinate_frame: str
    robot_id: UUID | None = None
    device_id: UUID | None = None
    status: LifecycleStatus | str = LifecycleStatus.PROVISIONED
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    resolution_w: int | None = None
    resolution_h: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    intrinsic_calibration_id: UUID | None = None
    extrinsic_calibration_id: UUID | None = None
    sync_mode: SensorSyncMode | str = SensorSyncMode.NONE
    sync_group: str | None = None
    firmware_version: str | None = None
    calibration_profile_id: UUID | None = None
    mount_position: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "sensor_type",
            coerce_enum(self.sensor_type, SensorType, "sensor_type"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, LifecycleStatus, "status"),
        )
        object.__setattr__(
            self,
            "sync_mode",
            coerce_enum(self.sync_mode, SensorSyncMode, "sync_mode"),
        )
        object.__setattr__(
            self,
            "sample_rate_hz",
            positive_float(self.sample_rate_hz, "sample_rate_hz"),
        )
        object.__setattr__(
            self,
            "coordinate_frame",
            normalized_text(
                self.coordinate_frame,
                "coordinate_frame",
                max_length=FRAME_MAX_LENGTH,
            ),
        )
        for name in ("vendor", "model", "serial_number"):
            object.__setattr__(
                self,
                name,
                optional_text(getattr(self, name), name, max_length=LABEL_MAX_LENGTH),
            )
        object.__setattr__(
            self,
            "sync_group",
            optional_text(self.sync_group, "sync_group", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "firmware_version",
            optional_text(
                self.firmware_version,
                "firmware_version",
                max_length=VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "mount_position",
            optional_text(
                self.mount_position,
                "mount_position",
                max_length=FRAME_MAX_LENGTH,
            ),
        )

        if self.robot_id is None and self.device_id is None:
            raise DomainValidationError("传感器必须至少关联机器人或设备")
        self._validate_resolution()
        for name in ("bit_depth", "channels"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, positive_int(value, name))
        if self.version < 1:
            raise DomainValidationError("version 必须大于等于 1")

    def _validate_resolution(self) -> None:
        if (self.resolution_w is None) != (self.resolution_h is None):
            raise DomainValidationError("resolution_w 与 resolution_h 必须同时提供")
        if self.resolution_w is not None:
            object.__setattr__(
                self,
                "resolution_w",
                positive_int(self.resolution_w, "resolution_w"),
            )
        if self.resolution_h is not None:
            object.__setattr__(
                self,
                "resolution_h",
                positive_int(self.resolution_h, "resolution_h"),
            )
