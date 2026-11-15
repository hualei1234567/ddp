"""标定参数测试夹具：每个 calibration_type 一份**合法最小参数**。

G-19 之后参数形状由契约 schema 强制，测试里随手写 `{"fx": 1.0}` 会被拒——
这正说明校验生效。集中在这里维护，避免各测试文件各写一份而漂移。
"""

from __future__ import annotations

VALID_PARAMETERS: dict[str, dict[str, object]] = {
    "INTRINSICS": {
        "camera_model": "PINHOLE",
        "image_width": 1280,
        "image_height": 720,
        "fx": 900.0,
        "fy": 900.0,
        "cx": 640.0,
        "cy": 360.0,
        "distortion_model": "plumb_bob",
        "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
        "unit": "px",
    },
    "EXTRINSICS": {
        "translation_m": [0.1, 0.2, 0.3],
        "rotation_quat_wxyz": [1.0, 0.0, 0.0, 0.0],
        "transform_type": "STATIC",
    },
    "HAND_EYE": {
        "eye_in_hand": True,
        "base_frame": "base_link",
        "tool_frame": "tool0",
        "camera_frame": "camera_link",
        "translation_m": [0.1, 0.2, 0.3],
        "rotation_quat_wxyz": [1.0, 0.0, 0.0, 0.0],
        "solver": "TSAI_LENZ",
    },
    "IMU": {
        "noise_density": {
            "accel_m_s2_per_sqrt_hz": 0.001,
            "gyro_rad_s_per_sqrt_hz": 0.0001,
        },
        "random_walk": {
            "accel_m_s3_per_sqrt_hz": 0.0001,
            "gyro_rad_s2_per_sqrt_hz": 0.00001,
        },
        "bias": {
            "accel_m_s2": [0.0, 0.0, 0.0],
            "gyro_rad_s": [0.0, 0.0, 0.0],
        },
        "gravity_m_s2": 9.81,
        # matrix3：3x3 矩阵，不是字符串标识。
        "axis_alignment": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "include_gravity": True,
    },
    "JOINT_OFFSET": {
        "joint_order": ["joint1", "joint2"],
        "joint_types": ["REVOLUTE", "REVOLUTE"],
        # offsets 是对象数组（关节名 + 值 + 单位），不是裸数字数组。
        "offsets": [
            {"joint_name": "joint1", "value": 0.0, "unit": "rad"},
            {"joint_name": "joint2", "value": 0.0, "unit": "rad"},
        ],
    },
    "TIME_OFFSET": {
        "sensor_id": "00000000-0000-0000-0000-000000000001",
        "reference_sensor_id": "00000000-0000-0000-0000-000000000002",
        # int64_string：超出 JS 安全整数范围的纳秒必须用字符串表示。
        "offset_ns": "0",
        "method": "HARDWARE_TRIGGER",
    },
    "FORCE_TORQUE": {
        "six_axis_order": ["fx", "fy", "fz", "tx", "ty", "tz"],
        "bias": [0.0] * 6,
        "unit_force": "N",
        "unit_torque": "N*m",
    },
    "DEPTH_ALIGNMENT": {
        "depth_sensor_id": "00000000-0000-0000-0000-000000000001",
        "rgb_sensor_id": "00000000-0000-0000-0000-000000000002",
        "target_resolution": [1280, 720],
        "depth_scale_m": 0.001,
        "invalid_value": 0,
        "translation_m": [0.0, 0.0, 0.0],
        "rotation_quat_wxyz": [1.0, 0.0, 0.0, 0.0],
    },
}


def parameters_for(calibration_type: str) -> dict[str, object]:
    """返回该类型的合法参数（深拷贝，调用方可安全修改）。"""
    import copy

    return copy.deepcopy(VALID_PARAMETERS[calibration_type])
