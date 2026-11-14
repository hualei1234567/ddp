"""设备域枚举。

取值与 DDP-101《阶段 1 领域模型与 API/数据契约草案》保持一致。
数据库使用 ``varchar + CHECK`` 保存枚举值，Domain 枚举是权威来源；
新增取值必须同时经过契约与迁移评审。
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleStatus(StrEnum):
    """机器人 / 设备 / 传感器共用的生命周期状态。"""

    PROVISIONED = "PROVISIONED"
    ACTIVE = "ACTIVE"
    IN_MAINTENANCE = "IN_MAINTENANCE"
    FAULT = "FAULT"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class DeviceType(StrEnum):
    """设备类型；厂商差异应放入 capability_profile，不在此处堆叠型号分支。"""

    COMPUTE = "COMPUTE"
    CONTROLLER = "CONTROLLER"
    CAMERA_RIG = "CAMERA_RIG"
    TELEOP = "TELEOP"
    GRIPPER = "GRIPPER"
    OTHER = "OTHER"


class SensorType(StrEnum):
    """传感器类型。"""

    RGB = "RGB"
    RGB_D = "RGB_D"
    DEPTH = "DEPTH"
    IMU = "IMU"
    JOINT_STATE = "JOINT_STATE"
    END_EFFECTOR = "END_EFFECTOR"
    FORCE_TORQUE = "FORCE_TORQUE"
    MOCAP = "MOCAP"
    AUDIO = "AUDIO"


class SensorSyncMode(StrEnum):
    """传感器同步方式，与 Canonical 草案一致。"""

    PTP = "PTP"
    NTP = "NTP"
    HARDWARE_TRIGGER = "HARDWARE_TRIGGER"
    SOFTWARE_ESTIMATE = "SOFTWARE_ESTIMATE"
    MANUAL = "MANUAL"
    NONE = "NONE"


class CalibrationScope(StrEnum):
    """标定作用域。"""

    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    SENSOR = "SENSOR"
    SYSTEM = "SYSTEM"


class CalibrationTargetType(StrEnum):
    """标定目标对象类型；与 DDP-101 §2.2 的 target_type 对齐。"""

    ROBOT = "ROBOT"
    DEVICE = "DEVICE"
    SENSOR = "SENSOR"
    SYSTEM = "SYSTEM"


class CalibrationType(StrEnum):
    """标定类型。"""

    INTRINSICS = "INTRINSICS"
    EXTRINSICS = "EXTRINSICS"
    HAND_EYE = "HAND_EYE"
    IMU = "IMU"
    JOINT_OFFSET = "JOINT_OFFSET"
    TIME_OFFSET = "TIME_OFFSET"
    FORCE_TORQUE = "FORCE_TORQUE"
    DEPTH_ALIGNMENT = "DEPTH_ALIGNMENT"


class CalibrationStatus(StrEnum):
    """标定记录状态。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class FrameConvention(StrEnum):
    """坐标系约定。

    ``UNSPECIFIED`` 是**显式的"不知道"**，不是默认猜一个：
    DDP 接入多厂商第一人称数据，把非 ROS 数据错标成 ROS 约定比标注"未知"更危险。
    """

    ROS_REP_103 = "ROS_REP_103"
    OPENCV = "OPENCV"
    UNITY_LHS = "UNITY_LHS"
    UNSPECIFIED = "UNSPECIFIED"
