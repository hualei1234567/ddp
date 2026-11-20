"""DROID 官方公开硬件事实到 DDP 既有设备字段的纯映射。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

_BLUEPRINT_SCHEMA_VERSION = "ddp.devices.public-hardware-blueprint/1.0.0"
_EXPECTED_REPOSITORY = "droid-dataset/droid"
_EXPECTED_CAMERA_COUNTS = {"ZED 2": 2, "ZED Mini": 1}
_EXPECTED_STREAMS = {
    "observation.images.exterior_image_1_left",
    "observation.images.exterior_image_2_left",
    "observation.images.wrist_image_left",
}


class DroidHardwareError(ValueError):
    """公开硬件事实缺失、冲突或超出当前受支持结构。"""


def build_droid_hardware_blueprint(facts: object) -> dict[str, object]:
    """把固定来源事实映射为可交给现有 Robot/Device/Sensor API 的蓝图。

    不生成资源 ID、序列号、固件、硬件版本、标定或同步声明；这些事实公开来源
    没有提供，必须由部署现场补录。
    """
    root = _mapping(facts, "facts")
    source = _mapping(root.get("source"), "source")
    if _text(source.get("repository"), "source.repository") != _EXPECTED_REPOSITORY:
        raise DroidHardwareError("source.repository 不是受支持的 DROID 官方仓库")
    commit = _git_sha(source.get("commit"), "source.commit")
    dataset_revision = _git_sha(
        source.get("dataset_revision"), "source.dataset_revision"
    )

    robot = _mapping(root.get("robot"), "robot")
    if _positive_int(robot.get("quantity"), "robot.quantity") != 1:
        raise DroidHardwareError("robot.quantity 必须为 1")
    num_dofs = _positive_int(robot.get("num_dofs"), "robot.num_dofs")
    if num_dofs != 7:
        raise DroidHardwareError("robot.num_dofs 必须为 7")

    gripper = _mapping(root.get("gripper"), "gripper")
    if _positive_int(gripper.get("quantity"), "gripper.quantity") != 1:
        raise DroidHardwareError("gripper.quantity 必须为 1")
    if _text(gripper.get("model"), "gripper.model") != "2F-85":
        raise DroidHardwareError("gripper.model 必须为公开清单中的 2F-85")

    camera_counts = _camera_counts(root.get("cameras"))
    if camera_counts != _EXPECTED_CAMERA_COUNTS:
        raise DroidHardwareError("cameras 与公开清单数量不一致")
    assignments = _stream_assignments(root.get("stream_models"))
    streams = _sensor_blueprints(root.get("video_streams"), assignments)

    source_payload: dict[str, object] = {
        "repository": _EXPECTED_REPOSITORY,
        "commit": commit,
        "license": source.get("license"),
        "dataset_id": _text(source.get("dataset_id"), "source.dataset_id"),
        "dataset_revision": dataset_revision,
        "dataset_license": _text(
            source.get("dataset_license"), "source.dataset_license"
        ),
    }
    return {
        "schema_version": _BLUEPRINT_SCHEMA_VERSION,
        "source": source_payload,
        "robot": {
            "code": "droid-franka",
            "name": "DROID Franka 机器人",
            "robot_type": "MANIPULATOR",
            "vendor": _text(robot.get("vendor"), "robot.vendor"),
            "model": _text(robot.get("model"), "robot.model"),
            "serial_number": None,
            "firmware_version": None,
            "hardware_version": None,
            "capability_profile": {
                "num_dofs": num_dofs,
                "source_commit": commit,
            },
        },
        "devices": [
            {
                "code": "droid-gripper",
                "name": "DROID Robotiq 夹爪",
                "device_type": "GRIPPER",
                "vendor": _text(gripper.get("vendor"), "gripper.vendor"),
                "model": "2F-85",
                "serial_number": None,
                "firmware_version": None,
                "hardware_version": None,
                "capability_profile": {"source_commit": commit},
            },
            {
                "code": "droid-camera-rig",
                "name": "DROID 三相机机架",
                "device_type": "CAMERA_RIG",
                "vendor": "Stereolabs",
                "model": "ZED 2 + ZED Mini",
                "serial_number": None,
                "firmware_version": None,
                "hardware_version": None,
                "capability_profile": {
                    "camera_counts": camera_counts,
                    "source_commit": commit,
                    "dataset_revision": dataset_revision,
                },
            },
        ],
        "sensors": streams,
    }


def _camera_counts(value: object) -> dict[str, int]:
    rows = _sequence(value, "cameras")
    counts: dict[str, int] = {}
    for index, raw in enumerate(rows):
        item = _mapping(raw, f"cameras[{index}]")
        if _text(item.get("vendor"), f"cameras[{index}].vendor") != "Stereolabs":
            raise DroidHardwareError("camera vendor 必须为 Stereolabs")
        model = _text(item.get("model"), f"cameras[{index}].model")
        if model in counts:
            raise DroidHardwareError("camera model 重复")
        counts[model] = _positive_int(
            item.get("quantity"), f"cameras[{index}].quantity"
        )
    return counts


def _stream_assignments(value: object) -> dict[str, str]:
    assignments = _mapping(value, "stream_models")
    if set(assignments) != _EXPECTED_STREAMS:
        raise DroidHardwareError("stream_models 必须完整覆盖三路公开视频流")
    resolved = {
        key: _text(model, f"stream_models.{key}") for key, model in assignments.items()
    }
    if sorted(resolved.values()) != ["ZED 2", "ZED 2", "ZED Mini"]:
        raise DroidHardwareError("stream_models 与公开相机数量不一致")
    return resolved


def _sensor_blueprints(
    value: object, assignments: Mapping[str, str]
) -> list[dict[str, object]]:
    rows = _sequence(value, "video_streams")
    if len(rows) != 3:
        raise DroidHardwareError("video_streams 必须包含三路公开视频")
    sensors: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        item = _mapping(raw, f"video_streams[{index}]")
        stream_id = _text(item.get("stream_id"), f"video_streams[{index}].stream_id")
        if stream_id in seen or stream_id not in _EXPECTED_STREAMS:
            raise DroidHardwareError("video_streams 包含重复或未知 stream_id")
        seen.add(stream_id)
        role = _text(item.get("role"), f"video_streams[{index}].role")
        expected_role = "WRIST_FIRST_PERSON" if "wrist" in stream_id else "EXTERIOR"
        if role != expected_role:
            raise DroidHardwareError("video stream role 与公开字段名不一致")
        sensors.append(
            {
                "code": stream_id.removeprefix("observation.images.").replace("_", "-"),
                "sensor_type": "RGB",
                "vendor": "Stereolabs",
                "model": assignments[stream_id],
                "serial_number": None,
                "sample_rate_hz": _positive_number(
                    item.get("fps"), f"video_streams[{index}].fps"
                ),
                "resolution_w": _positive_int(
                    item.get("width"), f"video_streams[{index}].width"
                ),
                "resolution_h": _positive_int(
                    item.get("height"), f"video_streams[{index}].height"
                ),
                "coordinate_frame": stream_id,
                "frame_id": None,
                "frame_convention": "UNSPECIFIED",
                "sync_mode": "NONE",
                "sync_group": None,
                "firmware_version": None,
                "mount_position": role,
            }
        )
    if seen != _EXPECTED_STREAMS:
        raise DroidHardwareError("video_streams 未完整覆盖三路公开视频流")
    return sorted(sensors, key=lambda item: str(item["code"]))


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise DroidHardwareError(f"{field} 必须是对象")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise DroidHardwareError(f"{field} 必须是数组")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DroidHardwareError(f"{field} 必须是无首尾空白的非空字符串")
    return value


def _git_sha(value: object, field: str) -> str:
    resolved = _text(value, field)
    if len(resolved) != 40 or any(char not in "0123456789abcdef" for char in resolved):
        raise DroidHardwareError(f"{field} 必须是 40 位小写 Git SHA")
    return resolved


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DroidHardwareError(f"{field} 必须是正整数")
    return value


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise DroidHardwareError(f"{field} 必须是正数")
    return float(value)


__all__ = ["DroidHardwareError", "build_droid_hardware_blueprint"]
