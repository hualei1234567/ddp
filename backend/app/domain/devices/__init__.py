"""设备、机器人、传感器与标定领域。"""

from app.domain.devices.calibration import CalibrationProfile, CalibrationRecord
from app.domain.devices.enums import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    DeviceType,
    LifecycleStatus,
    SensorSyncMode,
    SensorType,
)
from app.domain.devices.errors import (
    DeviceDomainError,
    DomainValidationError,
    DuplicateResourceError,
    InvalidStateTransitionError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.devices.models import Device, Robot, Sensor

__all__ = [
    "CalibrationProfile",
    "CalibrationRecord",
    "CalibrationScope",
    "CalibrationStatus",
    "CalibrationTargetType",
    "CalibrationType",
    "Device",
    "DeviceDomainError",
    "DeviceType",
    "DomainValidationError",
    "DuplicateResourceError",
    "InvalidStateTransitionError",
    "LifecycleStatus",
    "ResourceNotFoundError",
    "Robot",
    "Sensor",
    "SensorSyncMode",
    "SensorType",
    "VersionConflictError",
]
