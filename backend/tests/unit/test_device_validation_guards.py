"""设备域输入治理测试：长度、JSON 上限、深拷贝与浮点边界。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.domain.devices import Device, DeviceType, DomainValidationError, Robot, Sensor, SensorType
from app.domain.devices.validation import (
    CODE_MAX_LENGTH,
    JSON_MAX_BYTES,
    JSON_MAX_DEPTH,
    JSON_MAX_KEYS,
    positive_float,
)


def _robot(**overrides: object) -> Robot:
    values: dict[str, object] = {
        "robot_id": uuid4(),
        "project_id": uuid4(),
        "code": "robot-1",
        "name": "机器人一",
        "robot_type": "HUMANOID",
    }
    values.update(overrides)
    return Robot(**values)  # type: ignore[arg-type]


def _device(**overrides: object) -> Device:
    values: dict[str, object] = {
        "device_id": uuid4(),
        "project_id": uuid4(),
        "code": "device-1",
        "name": "设备一",
        "device_type": DeviceType.COMPUTE,
    }
    values.update(overrides)
    return Device(**values)  # type: ignore[arg-type]


def _sensor(**overrides: object) -> Sensor:
    values: dict[str, object] = {
        "sensor_id": uuid4(),
        "project_id": uuid4(),
        "code": "sensor-1",
        "sensor_type": SensorType.RGB,
        "sample_rate_hz": 30.0,
        "coordinate_frame": "camera_link",
        "robot_id": uuid4(),
    }
    values.update(overrides)
    return Sensor(**values)  # type: ignore[arg-type]


def test_text_field_accepts_boundary_length_and_rejects_overflow() -> None:
    assert len(_robot(code="c" * CODE_MAX_LENGTH).code) == CODE_MAX_LENGTH

    with pytest.raises(DomainValidationError, match="长度"):
        _robot(code="c" * (CODE_MAX_LENGTH + 1))


def test_optional_text_fields_are_bounded() -> None:
    with pytest.raises(DomainValidationError, match="长度"):
        _device(serial_number="s" * 121)
    with pytest.raises(DomainValidationError, match="长度"):
        _sensor(coordinate_frame="f" * 201)


def test_json_field_rejects_oversized_payload() -> None:
    with pytest.raises(DomainValidationError, match="字节"):
        _device(capability_profile={"blob": "x" * (JSON_MAX_BYTES + 1)})


def test_json_field_rejects_excessive_depth() -> None:
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(JSON_MAX_DEPTH + 2):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child

    with pytest.raises(DomainValidationError, match="深度"):
        _device(capability_profile=nested)


def test_json_field_rejects_excessive_keys() -> None:
    payload = {f"key-{index}": index for index in range(JSON_MAX_KEYS + 1)}

    with pytest.raises(DomainValidationError, match="键总数"):
        _device(capability_profile=payload)


def test_json_field_rejects_non_string_keys_and_non_finite_floats() -> None:
    with pytest.raises(DomainValidationError, match="键"):
        _device(capability_profile={1: "value"})  # type: ignore[dict-item]
    with pytest.raises(DomainValidationError, match="NaN"):
        _device(capability_profile={"value": float("nan")})
    with pytest.raises(DomainValidationError, match="NaN"):
        _device(capability_profile={"value": float("inf")})


def test_frozen_mapping_deep_copies_nested_values() -> None:
    nested_list = ["原始"]
    source: dict[str, object] = {
        "nested": {"items": nested_list},
    }

    device = _device(capability_profile=source)

    nested_list.append("外部修改")
    source["nested"] = {"items": ["替换"]}  # type: ignore[dict-item]

    stored = device.capability_profile["nested"]
    assert isinstance(stored, dict)
    assert stored["items"] == ["原始"]


def test_positive_float_rejects_nan_and_infinity() -> None:
    for value in (float("nan"), float("inf"), float("-inf"), 0, -1):
        with pytest.raises(DomainValidationError, match="有限正数"):
            positive_float(value, "sample_rate_hz")


def test_sensor_rejects_non_finite_sample_rate() -> None:
    with pytest.raises(DomainValidationError, match="有限正数"):
        _sensor(sample_rate_hz=float("inf"))


def test_error_messages_do_not_echo_input_values() -> None:
    marker = "INPUT-SECRET-MARKER"

    cases = (
        lambda: _device(device_type=marker),
        lambda: _robot(code=marker + "c" * CODE_MAX_LENGTH),
        lambda: _device(name="x" * 200 + marker),
    )
    for factory in cases:
        with pytest.raises(DomainValidationError) as error:
            factory()
        assert marker not in str(error.value)
