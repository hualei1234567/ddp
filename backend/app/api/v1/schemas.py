"""API DTO：请求约束与领域对象序列化。

请求模型统一 ``extra="forbid"``，客户端不能提交 status、project_id、
审计字段或 checksum 等服务端权威字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.models import Device, Robot, Sensor

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    device_type: str
    robot_id: UUID | None = None
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=64)
    hardware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    capability_profile: dict[str, object] = Field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    device_type: str | None = None
    robot_id: UUID | None = None
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=64)
    hardware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    capability_profile: dict[str, object] | None = None
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


class RobotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    robot_type: str = Field(min_length=1, max_length=64)
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=64)
    hardware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    capability_profile: dict[str, object] = Field(default_factory=dict)
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


class RobotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    robot_type: str | None = Field(default=None, min_length=1, max_length=64)
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=64)
    hardware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    capability_profile: dict[str, object] | None = None
    location_id: UUID | None = None
    owner_user_id: UUID | None = None


class RobotOut(BaseModel):
    robot_id: UUID
    project_id: UUID
    code: str
    name: str
    robot_type: str
    status: str
    vendor: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    hardware_version: str | None
    calibration_profile_id: UUID | None
    capability_profile: dict[str, object]
    location_id: UUID | None
    owner_user_id: UUID | None
    # 阶段 2 起设备域统一补了状态语义字段（G-09）；Robot 同样返回。
    status_reason: str | None = None
    fault_code: str | None = None
    fault_detail: str | None = None
    fault_occurred_at: datetime | None = None
    status_changed_at: datetime | None = None
    status_changed_by: UUID | None = None
    version: int

    @classmethod
    def from_domain(cls, robot: Robot) -> RobotOut:
        return cls(
            robot_id=robot.robot_id,
            project_id=robot.project_id,
            code=robot.code,
            name=robot.name,
            robot_type=robot.robot_type,
            status=_value(robot.status),
            vendor=robot.vendor,
            model=robot.model,
            serial_number=robot.serial_number,
            firmware_version=robot.firmware_version,
            hardware_version=robot.hardware_version,
            calibration_profile_id=robot.calibration_profile_id,
            capability_profile=dict(robot.capability_profile),
            location_id=robot.location_id,
            owner_user_id=robot.owner_user_id,
            status_reason=robot.status_reason,
            fault_code=robot.fault_code,
            fault_detail=robot.fault_detail,
            fault_occurred_at=robot.fault_occurred_at,
            status_changed_at=robot.status_changed_at,
            status_changed_by=robot.status_changed_by,
            version=robot.version,
        )


class CalibrationProfileCreate(BaseModel):
    """登记一个新版本；改配置 = 新建版本（ADR-0020 / G-07）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    scope: str
    target_type: str
    schema_version: str = Field(min_length=1, max_length=64)
    required_types: list[str] = Field(default_factory=list)
    selection_rule: dict[str, object] = Field(default_factory=dict)
    target_id: UUID | None = None
    # ADR-0032（G-03）：字段名、ORM 列、领域字段统一为 profile_version。
    profile_version: int = Field(default=1, ge=1)
    supersedes_id: UUID | None = None


class CalibrationProfileOut(BaseModel):
    profile_id: UUID
    project_id: UUID
    name: str
    scope: str
    target_type: str
    profile_version: int
    schema_version: str
    required_types: list[str]
    selection_rule: dict[str, object]
    target_id: UUID | None
    # 版本链：本版替代的上一版；首版为 null。
    supersedes_id: UUID | None = None

    @classmethod
    def from_domain(cls, profile: CalibrationProfile) -> CalibrationProfileOut:
        return cls(
            profile_id=profile.profile_id,
            project_id=profile.project_id,
            name=profile.name,
            scope=_value(profile.scope),
            target_type=_value(profile.target_type),
            profile_version=profile.profile_version,
            schema_version=profile.schema_version,
            required_types=[_value(item) for item in profile.required_types],
            selection_rule=dict(profile.selection_rule),
            target_id=profile.target_id,
            supersedes_id=profile.supersedes_id,
        )


class SensorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    sensor_type: str
    sample_rate_hz: float = Field(gt=0)
    coordinate_frame: str = Field(min_length=1, max_length=200)
    # ADR-0019：统一填写 frame_id；只给 coordinate_frame 时服务端回落。
    frame_id: str | None = Field(default=None, min_length=1, max_length=200)
    frame_convention: str = Field(
        default="UNSPECIFIED",
        pattern="^(ROS_REP_103|OPENCV|UNITY_LHS|UNSPECIFIED)$",
    )
    robot_id: UUID | None = None
    device_id: UUID | None = None
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    resolution_w: int | None = Field(default=None, gt=0)
    resolution_h: int | None = Field(default=None, gt=0)
    bit_depth: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    intrinsic_calibration_id: UUID | None = None
    extrinsic_calibration_id: UUID | None = None
    sync_mode: str = "NONE"
    sync_group: str | None = Field(default=None, max_length=64)
    firmware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    mount_position: str | None = Field(default=None, max_length=200)


class SensorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    sensor_type: str | None = None
    robot_id: UUID | None = None
    device_id: UUID | None = None
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    sample_rate_hz: float | None = Field(default=None, gt=0)
    resolution_w: int | None = Field(default=None, gt=0)
    resolution_h: int | None = Field(default=None, gt=0)
    bit_depth: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, gt=0)
    intrinsic_calibration_id: UUID | None = None
    extrinsic_calibration_id: UUID | None = None
    coordinate_frame: str | None = Field(default=None, min_length=1, max_length=200)
    frame_id: str | None = Field(default=None, min_length=1, max_length=200)
    frame_convention: str | None = Field(
        default=None,
        pattern="^(ROS_REP_103|OPENCV|UNITY_LHS|UNSPECIFIED)$",
    )
    sync_mode: str | None = None
    sync_group: str | None = Field(default=None, max_length=64)
    firmware_version: str | None = Field(default=None, max_length=64)
    calibration_profile_id: UUID | None = None
    mount_position: str | None = Field(default=None, max_length=200)


class CalibrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    target_type: str
    target_id: UUID
    calibration_type: str
    method: str = Field(min_length=1, max_length=200)
    parameters: dict[str, object]
    valid_from: datetime
    valid_to: datetime | None = None
    parent_frame: str | None = Field(default=None, max_length=200)
    child_frame: str | None = Field(default=None, max_length=200)
    evidence_asset_ids: list[UUID] = Field(default_factory=list)
    source: str | None = Field(default=None, max_length=120)
    uncertainty: dict[str, object] = Field(default_factory=dict)


class CalibrationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID | None = None
    target_type: str | None = None
    target_id: UUID | None = None
    calibration_type: str | None = None
    method: str | None = Field(default=None, min_length=1, max_length=200)
    parameters: dict[str, object] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    parent_frame: str | None = Field(default=None, max_length=200)
    child_frame: str | None = Field(default=None, max_length=200)
    evidence_asset_ids: list[UUID] | None = None
    source: str | None = Field(default=None, max_length=120)
    uncertainty: dict[str, object] | None = None


class ActionRequest(BaseModel):
    """状态动作的统一可选载荷。

    字段分两类，**不要混为一谈**：

    - 已被实现读取并落库：``reason`` / ``fault_code`` / ``fault_detail`` /
      ``occurred_at``。Device / Sensor / Robot 三条路由都把前三个透传给用例，
      ``occurred_at`` 映射为 ``fault_occurred_at``；审计与 outbox 事件同样带这些值。
    - **契约保留但实现从不读取**：``maintenance_record_id`` / ``evidence_refs``。
      二者只因阶段 1 冻结文档 §4.3/§6.1/§8.1 声明过而留在 schema 里；
      后端没有任何代码读取它们，提交也不会持久化，不会报错。属于"契约声明了
      但实现没做"，已登记在契约一致性核查报告中（`scripts/check_contract_parity.py`）。
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    fault_code: str | None = Field(default=None, max_length=64)
    fault_detail: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    maintenance_record_id: UUID | None = None
    evidence_refs: list[UUID] | None = None


class DeviceOut(BaseModel):
    device_id: UUID
    project_id: UUID
    robot_id: UUID | None
    code: str
    name: str
    device_type: str
    vendor: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    hardware_version: str | None
    calibration_profile_id: UUID | None
    capability_profile: dict[str, object]
    status: str
    location_id: UUID | None
    owner_user_id: UUID | None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None
    # G-09：状态语义六列在 DB 里早就有（迁移 0023），但此前只有 RobotOut 对外
    # 返回。客户端看不到"为什么进入该状态"，只能拿到一个孤立的 status。
    status_reason: str | None = None
    fault_code: str | None = None
    fault_detail: str | None = None
    fault_occurred_at: datetime | None = None
    status_changed_at: datetime | None = None
    status_changed_by: UUID | None = None

    @classmethod
    def from_domain(cls, device: Device) -> DeviceOut:
        return cls(
            device_id=device.device_id,
            project_id=device.project_id,
            robot_id=device.robot_id,
            code=device.code,
            name=device.name,
            device_type=_value(device.device_type),
            vendor=device.vendor,
            model=device.model,
            serial_number=device.serial_number,
            firmware_version=device.firmware_version,
            hardware_version=device.hardware_version,
            calibration_profile_id=device.calibration_profile_id,
            capability_profile=dict(device.capability_profile),
            status=_value(device.status),
            location_id=device.location_id,
            owner_user_id=device.owner_user_id,
            version=device.version,
            status_reason=device.status_reason,
            fault_code=device.fault_code,
            fault_detail=device.fault_detail,
            fault_occurred_at=device.fault_occurred_at,
            status_changed_at=device.status_changed_at,
            status_changed_by=device.status_changed_by,
        )


class SensorOut(BaseModel):
    sensor_id: UUID
    project_id: UUID
    robot_id: UUID | None
    device_id: UUID | None
    code: str
    sensor_type: str
    vendor: str | None
    model: str | None
    serial_number: str | None
    sample_rate_hz: float
    resolution_w: int | None
    resolution_h: int | None
    bit_depth: int | None
    channels: int | None
    intrinsic_calibration_id: UUID | None
    extrinsic_calibration_id: UUID | None
    coordinate_frame: str
    frame_id: str
    frame_convention: str
    sync_mode: str
    sync_group: str | None
    firmware_version: str | None
    calibration_profile_id: UUID | None
    status: str
    mount_position: str | None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # G-09：与 DeviceOut / RobotOut 对齐的状态语义六列。
    status_reason: str | None = None
    fault_code: str | None = None
    fault_detail: str | None = None
    fault_occurred_at: datetime | None = None
    status_changed_at: datetime | None = None
    status_changed_by: UUID | None = None

    @classmethod
    def from_domain(cls, sensor: Sensor) -> SensorOut:
        return cls(
            sensor_id=sensor.sensor_id,
            project_id=sensor.project_id,
            robot_id=sensor.robot_id,
            device_id=sensor.device_id,
            code=sensor.code,
            sensor_type=_value(sensor.sensor_type),
            vendor=sensor.vendor,
            model=sensor.model,
            serial_number=sensor.serial_number,
            sample_rate_hz=sensor.sample_rate_hz,
            resolution_w=sensor.resolution_w,
            resolution_h=sensor.resolution_h,
            bit_depth=sensor.bit_depth,
            channels=sensor.channels,
            intrinsic_calibration_id=sensor.intrinsic_calibration_id,
            extrinsic_calibration_id=sensor.extrinsic_calibration_id,
            coordinate_frame=sensor.coordinate_frame,
            frame_id=sensor.frame_id or sensor.coordinate_frame,
            frame_convention=str(sensor.frame_convention),
            sync_mode=_value(sensor.sync_mode),
            sync_group=sensor.sync_group,
            firmware_version=sensor.firmware_version,
            calibration_profile_id=sensor.calibration_profile_id,
            status=_value(sensor.status),
            mount_position=sensor.mount_position,
            version=sensor.version,
            status_reason=sensor.status_reason,
            fault_code=sensor.fault_code,
            fault_detail=sensor.fault_detail,
            fault_occurred_at=sensor.fault_occurred_at,
            status_changed_at=sensor.status_changed_at,
            status_changed_by=sensor.status_changed_by,
        )


class CalibrationOut(BaseModel):
    calibration_id: UUID
    project_id: UUID
    profile_id: UUID
    scope: str
    target_type: str
    target_id: UUID
    calibration_type: str
    method: str
    parameters: dict[str, object]
    parent_frame: str | None
    child_frame: str | None
    valid_from: datetime
    valid_to: datetime | None
    status: str
    evidence_asset_ids: list[UUID]
    source: str | None
    checksum: str
    uncertainty: dict[str, object]
    performed_by: UUID
    validated_by: UUID | None
    validated_at: datetime | None
    supersedes_id: UUID | None
    # ADR-0020：业务修订号与乐观锁版本分离，两者语义不同，必须都返回。
    calibration_version: int
    profile_version: int | None
    profile_schema_version: str | None
    profile_snapshot: dict[str, object]
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_domain(cls, record: CalibrationRecord) -> CalibrationOut:
        return cls(
            calibration_id=record.calibration_id,
            project_id=record.project_id,
            profile_id=record.profile_id,
            scope=_value(record.scope),
            target_type=_value(record.target_type),
            target_id=record.target_id,
            calibration_type=_value(record.calibration_type),
            method=record.method,
            parameters=dict(record.parameters),
            parent_frame=record.parent_frame,
            child_frame=record.child_frame,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            status=_value(record.status),
            evidence_asset_ids=list(record.evidence_asset_ids),
            source=record.source,
            checksum=record.checksum,
            uncertainty=dict(record.uncertainty),
            performed_by=record.performed_by,
            validated_by=record.validated_by,
            validated_at=record.validated_at,
            supersedes_id=record.supersedes_id,
            calibration_version=record.calibration_version,
            profile_version=record.profile_version,
            profile_schema_version=record.profile_schema_version,
            profile_snapshot=dict(record.profile_snapshot),
            version=record.version,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
