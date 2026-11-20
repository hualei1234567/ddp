"""DROID 官方硬件清单 + DROID-100 schema 的严格设备蓝图。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ddp_data.droid.hardware import DroidHardwareError, build_droid_hardware_blueprint

_FIXTURE = Path(__file__).parents[1] / "fixtures/public/droid-hardware/facts.json"


def _facts() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_public_facts_build_robot_gripper_and_three_camera_sensors() -> None:
    blueprint = build_droid_hardware_blueprint(_facts())

    assert blueprint["schema_version"] == "ddp.devices.public-hardware-blueprint/1.0.0"
    assert blueprint["source"]["repository"] == "droid-dataset/droid"
    robot = blueprint["robot"]
    assert robot["vendor"] == "Franka Robotics"
    assert robot["model"] == "Franka Emika Panda / Franka Research 3"
    assert robot["serial_number"] is None
    assert robot["firmware_version"] is None
    assert robot["hardware_version"] is None
    assert robot["capability_profile"]["num_dofs"] == 7

    devices = blueprint["devices"]
    assert [(item["device_type"], item["model"]) for item in devices] == [
        ("GRIPPER", "2F-85"),
        ("CAMERA_RIG", "ZED 2 + ZED Mini"),
    ]
    assert all(item["serial_number"] is None for item in devices)

    sensors = blueprint["sensors"]
    assert len(sensors) == 3
    assert {item["code"] for item in sensors} == {
        "exterior-image-1-left",
        "exterior-image-2-left",
        "wrist-image-left",
    }
    assert {item["model"] for item in sensors} == {"ZED 2", "ZED Mini"}
    assert {item["sample_rate_hz"] for item in sensors} == {15.0}
    assert {(item["resolution_w"], item["resolution_h"]) for item in sensors} == {(320, 180)}
    assert all(item["sync_mode"] == "NONE" for item in sensors)
    assert all(item["serial_number"] is None for item in sensors)


def test_unknown_camera_assignment_is_not_guessed() -> None:
    facts = _facts()
    facts["stream_models"] = {
        "observation.images.exterior_image_1_left": "ZED 2",
    }

    with pytest.raises(DroidHardwareError, match="stream_models"):
        build_droid_hardware_blueprint(facts)


@pytest.mark.parametrize(
    "path,value",
    [
        (("robot", "quantity"), 2),
        (("gripper", "model"), "unknown"),
        (("video_streams", 0, "fps"), 0),
        (("video_streams", 0, "width"), True),
    ],
)
def test_invalid_or_unsupported_public_facts_fail_closed(
    path: tuple[object, ...], value: object
) -> None:
    facts = _facts()
    target: object = facts
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(DroidHardwareError):
        build_droid_hardware_blueprint(facts)
