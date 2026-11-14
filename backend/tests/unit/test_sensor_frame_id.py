"""ADR-0019 阶段 1：frame_id / frame_convention 领域规则。

阶段 1 的核心约束是"向后兼容但不含糊"：
- 只给 coordinate_frame 时回落填 frame_id；
- 两者都给且不一致时**报错**，不静默择一；
- frame_convention 缺省是 UNSPECIFIED（显式"不知道"），不是猜一个。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.devices.enums import FrameConvention
from app.domain.devices.errors import DomainValidationError
from app.domain.devices.models import Sensor


def _sensor(**overrides: object) -> Sensor:
    base: dict[str, object] = {
        "sensor_id": uuid4(),
        "project_id": uuid4(),
        "code": "sensor-a",
        "sensor_type": "RGB",
        "sample_rate_hz": 30.0,
        "coordinate_frame": "camera_link",
        "device_id": uuid4(),
    }
    base.update(overrides)
    return Sensor(**base)  # type: ignore[arg-type]


def test_frame_id_falls_back_to_coordinate_frame() -> None:
    sensor = _sensor()

    assert sensor.frame_id == "camera_link"


def test_explicit_frame_id_matching_is_accepted() -> None:
    sensor = _sensor(frame_id="camera_link")

    assert sensor.frame_id == "camera_link"


def test_mismatched_frame_id_is_rejected() -> None:
    """阶段 1 二者必须表达同一坐标系；不一致说明调用方理解有误。"""
    with pytest.raises(DomainValidationError, match="不一致"):
        _sensor(frame_id="base_link")


def test_convention_defaults_to_unspecified_not_ros() -> None:
    """默认不许猜：把非 ROS 数据错标成 ROS 约定比标注未知更危险。"""
    assert _sensor().frame_convention is FrameConvention.UNSPECIFIED


@pytest.mark.parametrize(
    "convention",
    ["ROS_REP_103", "OPENCV", "UNITY_LHS", "UNSPECIFIED"],
)
def test_all_documented_conventions_are_accepted(convention: str) -> None:
    assert _sensor(frame_convention=convention).frame_convention.value == convention


def test_unknown_convention_is_rejected() -> None:
    with pytest.raises(ValueError):
        _sensor(frame_convention="SOME_RANDOM")


def test_coordinate_frame_still_present_for_compatibility() -> None:
    """阶段 1 不移除旧字段：旧客户端不能因为这次迁移挂掉。"""
    assert _sensor().coordinate_frame == "camera_link"
