"""设备域用例输入命令。

命令只承载已解析的原始字段；业务规则仍在 Domain 与用例内执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NewDevice:
    code: str
    name: str
    device_type: str
    robot_id: UUID | None = None
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    calibration_profile_id: UUID | None = None
    capability_profile: Mapping[str, object] = field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewSensor:
    code: str
    sensor_type: str
    sample_rate_hz: float
    coordinate_frame: str
    frame_id: str | None = None
    frame_convention: str = "UNSPECIFIED"
    robot_id: UUID | None = None
    device_id: UUID | None = None
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    resolution_w: int | None = None
    resolution_h: int | None = None
    bit_depth: int | None = None
    channels: int | None = None
    intrinsic_calibration_id: UUID | None = None
    extrinsic_calibration_id: UUID | None = None
    sync_mode: str = "NONE"
    sync_group: str | None = None
    firmware_version: str | None = None
    calibration_profile_id: UUID | None = None
    mount_position: str | None = None


@dataclass(frozen=True, slots=True)
class NewCalibration:
    profile_id: UUID
    target_type: str
    target_id: UUID
    calibration_type: str
    method: str
    parameters: Mapping[str, object]
    valid_from: datetime
    valid_to: datetime | None = None
    parent_frame: str | None = None
    child_frame: str | None = None
    evidence_asset_ids: tuple[UUID, ...] = ()
    source: str | None = None
    uncertainty: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NewRobot:
    code: str
    name: str
    robot_type: str
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    calibration_profile_id: UUID | None = None
    capability_profile: Mapping[str, object] = field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewCalibrationProfile:
    """Profile 一行代表一个不可变配置版本；改配置 = 新建版本（ADR-0020 / G-07）。"""

    name: str
    scope: str
    target_type: str
    schema_version: str
    required_types: tuple[str, ...] = ()
    selection_rule: Mapping[str, object] = field(default_factory=dict)
    target_id: UUID | None = None
    profile_version: int = 1
    supersedes_id: UUID | None = None
