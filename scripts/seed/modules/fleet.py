"""设备域种子：标定配置 / 机器人 / 设备 / 传感器 / 标定记录。

覆盖的状态路径
--------------
- 标定配置：登记 v1，再登记 v2（``profile_version = 上一版 + 1`` 且
  ``supersedes_id`` 指向 v1）；
- 机器人：``PROVISIONED → ACTIVE``（A1 先补齐配置要求的 ACTIVE 标定）、
  ``ACTIVE → IN_MAINTENANCE``（M2）、``ACTIVE → RETIRED``（S3）、
  ``ACTIVE → FAULT``（A4）；
- 设备：``PROVISIONED → ACTIVE``（相机先补 EXTRINSICS）、``ACTIVE``（夹爪）、
  ``ACTIVE → FAULT → IN_MAINTENANCE → ACTIVE``（工控主机故障恢复）、
  ``ACTIVE → RETIRED``（遥操作主手）；
- 传感器：``PROVISIONED → ACTIVE``（RGB 先补 INTRINSICS 并回填
  ``intrinsic_calibration_id``）、``ACTIVE``（深度）、``ACTIVE → IN_MAINTENANCE``
  （关节状态）、``ACTIVE → RETIRED``（IMU）；
- 标定记录：四条走完 ``DRAFT → SUBMITTED → VALIDATED → ACTIVE``（其中一条复标
  激活时把同目标同类型旧 ACTIVE 原子转成 ``SUPERSEDED``）；一条
  ``SUBMITTED → REJECTED``；一条 ``ACTIVE → REVOKED``。

关于证据：``DRAFT → SUBMITTED`` 要求 ``evidence_asset_ids`` 非空。本模块在
``run.py`` 的 MODULES 里排在数据源之前，此时还没有 raw asset；冻结契约
（``docs/architecture/阶段1-设备域契约冻结-v1.md:158``）要求证据"同项目且为
``VALID/ARCHIVED``"，但实现只在领域层校验非空
（``backend/app/domain/devices/calibration.py`` 的 ``submit``），没有引用查询也
没有外键。这里填显式占位 UUID，不冒充真实证据。

关于走不到的终态：``REJECTED → DRAFT`` 的迁移存在于
``backend/app/domain/devices/state_machines.py``，但没有对外动作，纯 HTTP 路径
无法重新打开被驳回的标定。``EXPIRED`` 可达（``ACTIVE → EXPIRED``），本模块不
重复造——三种终态已由 SUPERSEDED / REJECTED / REVOKED 覆盖。
"""

from __future__ import annotations

import uuid
from typing import Any

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "fleet"
SCHEMA_VERSION = "ddp.device.calibration-parameters/1.0.0"
VALID_FROM = "2026-10-01T00:00:00Z"
VALID_TO = "2027-10-01T00:00:00Z"


# --- HTTP 基座 -------------------------------------------------------------


def _create(
    client: Client, path: str, body: dict[str, Any], label: str, *, keyed: bool = True
) -> dict[str, Any]:
    headers = {"Idempotency-Key": idempotency_key()} if keyed else None
    payload = expect(label, client.call("POST", path, body=body, headers=headers), 201)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _act(
    client: Client,
    path: str,
    action: str,
    version: int,
    label: str,
    *,
    body: dict[str, Any] | None = None,
    keyed: bool = True,
) -> dict[str, Any]:
    headers = if_match(version)
    if keyed:
        headers["Idempotency-Key"] = idempotency_key()
    payload = expect(
        label, client.call("POST", f"{path}/{action}", body=body, headers=headers), 200
    )
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _patch(
    client: Client, path: str, version: int, body: dict[str, Any], label: str
) -> dict[str, Any]:
    payload = expect(label, client.call("PATCH", path, body=body, headers=if_match(version)), 200)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _step(
    client: Client,
    path: str,
    action: str,
    version: int,
    want_status: str,
    label: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _act(client, path, action, version, label, body=body)
    actual = payload.get("status")
    if actual != want_status:
        raise SeedFailure(f"{label}：期望 status={want_status}，实际 {actual}")
    return payload


# --- 请求体构造 ------------------------------------------------------------


def _evidence() -> list[str]:
    """占位证据 UUID；理由见模块头。"""
    return [str(uuid.uuid4()), str(uuid.uuid4())]


def _intrinsics(fx: float, fy: float, cx: float, cy: float) -> dict[str, Any]:
    return {
        "camera_model": "PINHOLE",
        "image_width": 1280,
        "image_height": 720,
        "fx": fx,
        "fy": fy,
        "cx": cx,
        "cy": cy,
        "distortion_model": "plumb_bob",
        "distortion_coefficients": [-0.0412, 0.0186, 0.0007, -0.0003, 0.0],
        "unit": "px",
    }


def _extrinsics(translation: list[float], rotation: list[float]) -> dict[str, Any]:
    return {
        "translation_m": translation,
        "rotation_quat_wxyz": rotation,
        "transform_type": "STATIC",
    }


def _robot(
    code: str,
    name: str,
    robot_type: str,
    *,
    vendor: str,
    model: str,
    serial: str,
    firmware: str,
    capability: dict[str, Any],
    hardware: str | None = None,
    profile: str | None = None,
    owner: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "robot_type": robot_type,
        "vendor": vendor,
        "model": model,
        "serial_number": serial,
        "firmware_version": firmware,
        "hardware_version": hardware,
        "calibration_profile_id": profile,
        "capability_profile": capability,
        "owner_user_id": owner,
    }


def _device(
    code: str,
    name: str,
    device_type: str,
    robot_id: str,
    *,
    vendor: str,
    model: str,
    serial: str,
    firmware: str,
    capability: dict[str, Any],
    profile: str | None = None,
    owner: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "device_type": device_type,
        "robot_id": robot_id,
        "vendor": vendor,
        "model": model,
        "serial_number": serial,
        "firmware_version": firmware,
        "calibration_profile_id": profile,
        "capability_profile": capability,
        "owner_user_id": owner,
    }


def _sensor(**fields: Any) -> dict[str, Any]:
    """传感器请求体；``frame`` 同时写入已废弃的 ``coordinate_frame`` 与新字段。"""
    frame = fields.pop("frame")
    body: dict[str, Any] = {
        "coordinate_frame": frame,
        "frame_id": frame,
        "frame_convention": "ROS_REP_103",
        "sync_mode": fields.pop("sync", "NONE"),
        "mount_position": fields.pop("mount", None),
        **fields,
    }
    return body


def _calibration_body(
    profile: str,
    target_type: str,
    target_id: str,
    calibration_type: str,
    method: str,
    parameters: dict[str, Any],
    *,
    frames: tuple[str, str] | None = None,
    uncertainty: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "profile_id": profile,
        "target_type": target_type,
        "target_id": target_id,
        "calibration_type": calibration_type,
        "method": method,
        "parameters": parameters,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
        "evidence_asset_ids": _evidence(),
        "source": source,
        "uncertainty": uncertainty,
    }
    if frames is not None:
        body["parent_frame"], body["child_frame"] = frames
    return body


# --- 资源创建 --------------------------------------------------------------


def _profiles(client: Client, base: str) -> tuple[str, str, str]:
    path = f"{base}/calibration-profiles"
    # (名称, scope, 要求类型, 选择规则最大年龄, profile_version)
    specs = (
        ("双臂机器人整机标定配置 v1", "ROBOT", ["INTRINSICS", "EXTRINSICS", "HAND_EYE"], 365, 1),
        ("双臂机器人整机标定配置 v2", "ROBOT", ["INTRINSICS", "EXTRINSICS", "HAND_EYE"], 180, 2),
        ("相机模组外参标定配置", "DEVICE", ["EXTRINSICS"], 180, 1),
        ("RGB 相机内参标定配置", "SENSOR", ["INTRINSICS"], 365, 1),
    )
    created: list[dict[str, Any]] = []
    for name, scope, required, max_age, version in specs:
        body: dict[str, Any] = {
            "name": name,
            "scope": scope,
            "target_type": scope,
            "schema_version": SCHEMA_VERSION,
            "required_types": required,
            "selection_rule": {"prefer": "latest_active", "max_age_days": max_age},
            "profile_version": version,
        }
        if name.endswith("v2"):
            # 契约要求 profile_version=上一版+1，且不得分叉。
            body["supersedes_id"] = str(created[0]["profile_id"])
        created.append(_create(client, path, body, f"登记{name}"))
    return (
        str(created[1]["profile_id"]),
        str(created[2]["profile_id"]),
        str(created[3]["profile_id"]),
    )


def _robots(
    client: Client, base: str, stamp: str, profile: str, owner: str
) -> dict[str, dict[str, Any]]:
    specs = (
        ("primary", "a1", "家庭服务双臂机器人 A1", "DUAL_ARM_MOBILE", "智元机器人"),
        ("maintenance", "m2", "轮式移动底盘 M2", "AMR", "松灵机器人"),
        ("retired", "s3", "桌面单臂实验台 S3", "DESKTOP_ARM", "优傲机器人"),
        ("faulty", "a4", "家庭服务双臂机器人 A4", "DUAL_ARM_MOBILE", "智元机器人"),
    )
    models = {
        "a1": ("AgiBot A2-D", "H3", {"arm_dof": 7, "payload_kg": 5, "camera_count": 3}, True),
        "m2": ("Scout Mini", None, {"max_speed_mps": 1.5, "payload_kg": 20, "lidar": "2D"}, False),
        "s3": ("UR5e", None, {"arm_dof": 6, "payload_kg": 5}, False),
        "a4": ("AgiBot A2-D", None, {"arm_dof": 7, "payload_kg": 5}, False),
    }
    robots: dict[str, dict[str, Any]] = {}
    for key, suffix, name, robot_type, vendor in specs:
        model, hardware, capability, bind_profile = models[suffix]
        robots[key] = _create(
            client,
            f"{base}/robots",
            _robot(
                f"robot-{stamp}-{suffix}",
                name,
                robot_type,
                vendor=vendor,
                model=model,
                serial=f"AGI-{suffix.upper()}-{stamp}",
                firmware="v4.2.1" if suffix in {"a1", "a4"} else "v3.1.0",
                hardware=hardware,
                capability=capability,
                profile=profile if bind_profile else None,
                owner=owner,
            ),
            f"创建机器人 {name}",
        )
    return robots


def _devices(
    client: Client, base: str, stamp: str, robot: str, profile: str, owner: str
) -> dict[str, dict[str, Any]]:
    specs = (
        (
            "camera",
            "cam",
            "头部双目相机模组",
            "CAMERA_RIG",
            "Intel",
            "RealSense D455",
            {"baseline_mm": 95, "depth_range_m": [0.3, 6.0]},
            True,
        ),
        (
            "gripper",
            "grip",
            "六维力控两指夹爪",
            "GRIPPER",
            "Robotiq",
            "Hand-E",
            {"stroke_mm": 50, "max_force_n": 130},
            False,
        ),
        (
            "host",
            "host",
            "采集工控主机",
            "COMPUTE",
            "NVIDIA",
            "Jetson AGX Orin 64GB",
            {"cpu_cores": 12, "ram_gb": 64, "storage_gb": 2048},
            False,
        ),
        (
            "teleop",
            "teleop",
            "遥操作主手",
            "TELEOP",
            "Dobot",
            "Nova 2",
            {"dof": 6, "workspace_m": 0.6},
            False,
        ),
    )
    devices: dict[str, dict[str, Any]] = {}
    for key, suffix, name, device_type, vendor, model, capability, bind_profile in specs:
        devices[key] = _create(
            client,
            f"{base}/devices",
            _device(
                f"dev-{stamp}-{suffix}",
                name,
                device_type,
                robot,
                vendor=vendor,
                model=model,
                serial=f"DEV-{suffix.upper()}-{stamp}",
                firmware="5.13.0.50" if device_type == "CAMERA_RIG" else "v2.8.4",
                capability=capability,
                profile=profile if bind_profile else None,
                owner=owner,
            ),
            f"创建{name}",
        )
    return devices


def _sensors(
    client: Client, base: str, stamp: str, camera: str, robot: str, profile: str
) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {
        "rgb": dict(
            sensor_type="RGB",
            sample_rate_hz=30,
            frame="camera_color_optical_frame",
            vendor="Intel",
            model="RealSense D455 RGB",
            resolution_w=1280,
            resolution_h=720,
            bit_depth=8,
            channels=3,
            sync="HARDWARE_TRIGGER",
            mount="头部前方，离地 1.20m",
            device_id=camera,
            robot_id=robot,
            calibration_profile_id=profile,
        ),
        "depth": dict(
            sensor_type="DEPTH",
            sample_rate_hz=30,
            frame="camera_depth_optical_frame",
            vendor="Intel",
            model="RealSense D455 Depth",
            resolution_w=848,
            resolution_h=480,
            bit_depth=16,
            channels=1,
            sync="HARDWARE_TRIGGER",
            mount="头部前方，与 RGB 基线 95mm",
            device_id=camera,
            robot_id=robot,
        ),
        "joint": dict(
            sensor_type="JOINT_STATE",
            sample_rate_hz=500,
            frame="base_link",
            vendor="智元机器人",
            model="A2-D Joint Encoder",
            channels=14,
            sync="PTP",
            sync_group=f"robot-{stamp}",
            mount="双臂 14 个关节编码器",
            robot_id=robot,
        ),
        "imu": dict(
            sensor_type="IMU",
            sample_rate_hz=200,
            frame="imu_link",
            vendor="Bosch",
            model="BMI088",
            channels=9,
            sync="PTP",
            sync_group=f"robot-{stamp}",
            mount="躯干中心 IMU 座",
            robot_id=robot,
        ),
    }
    sensors: dict[str, dict[str, Any]] = {}
    for key, fields in specs.items():
        sensors[key] = _create(
            client,
            f"{base}/sensors",
            _sensor(
                code=f"sensor-{stamp}-{key}",
                serial_number=f"SENSOR-{key.upper()}-{stamp}",
                firmware_version="5.13.0.50" if key in {"rgb", "depth"} else "v1.6.0",
                sync_group=fields.pop("sync_group", f"cam-{stamp}"),
                **fields,
            ),
            f"创建 {key} 传感器",
        )
    return sensors


# --- 标定记录 --------------------------------------------------------------


def _calibration(
    client: Client, base: str, body: dict[str, Any], label: str
) -> tuple[dict[str, Any], str]:
    record = _create(client, f"{base}/calibrations", body, label, keyed=False)
    return record, f"{base}/calibrations/{record['calibration_id']}"


def _approve(
    ctx: Context, client: Client, path: str, record: dict[str, Any], label: str
) -> dict[str, Any]:
    record = _act(client, path, "submit", record["version"], f"{label}：提交", keyed=False)
    # 四眼原则（domain/devices/calibration.py 的 validate）：校验人必须不是执行人。
    record = _act(
        ctx.as_("reviewer"), path, "validate", record["version"], f"{label}：校验", keyed=False
    )
    return _act(client, path, "activate", record["version"], f"{label}：激活", keyed=False)


def _calibrate_robot(ctx: Context, client: Client, base: str, robot: str, profile: str) -> None:
    bench = "临港标定间-双目标定台"
    specs = (
        (
            "INTRINSICS",
            "棋盘格张正友标定（9x6，2.5cm）",
            _intrinsics(912.47, 911.83, 642.15, 361.42),
            None,
            {"reprojection_error_px": 0.32},
            bench,
        ),
        (
            "EXTRINSICS",
            "激光跟踪仪基座-相机外参标定",
            _extrinsics([0.312, -0.045, 1.218], [0.7071068, 0.0, 0.7071068, 0.0]),
            ("base_link", "camera_link"),
            {"translation_m": 0.0015, "rotation_rad": 0.0009},
            "临港标定间-激光跟踪仪",
        ),
        (
            "HAND_EYE",
            "OpenCV TSAI 手眼标定（20 组位姿）",
            {
                "eye_in_hand": True,
                "base_frame": "base_link",
                "tool_frame": "tool0",
                "camera_frame": "camera_color_optical_frame",
                "translation_m": [0.052, -0.081, 0.126],
                "rotation_quat_wxyz": [0.5, -0.5, 0.5, -0.5],
                "solver": "opencv-calibrateHandEye-TSAI",
                "residual": {"translation_m": 0.004, "rotation_rad": 0.006},
            },
            ("base_link", "camera_color_optical_frame"),
            {"translation_m": 0.004, "rotation_rad": 0.006},
            "临港标定间-手眼标定台",
        ),
    )
    first_path: str | None = None
    for calibration_type, method, parameters, frames, uncertainty, source in specs:
        record, path = _calibration(
            client,
            base,
            _calibration_body(
                profile,
                "ROBOT",
                robot,
                calibration_type,
                method,
                parameters,
                frames=frames,
                uncertainty=uncertainty,
                source=source,
            ),
            f"创建机器人 {calibration_type} 标定",
        )
        _approve(ctx, client, path, record, f"机器人 {calibration_type} 标定")
        if calibration_type == "INTRINSICS":
            first_path = path

    # 复标：同目标同类型的第二条 ACTIVE 会让旧记录原子转成 SUPERSEDED。
    second, second_path = _calibration(
        client,
        base,
        _calibration_body(
            profile,
            "ROBOT",
            robot,
            "INTRINSICS",
            "棋盘格张正友标定（9x6，2.5cm，第二轮复标）",
            _intrinsics(914.02, 913.37, 641.88, 360.97),
            uncertainty={"reprojection_error_px": 0.27},
            source=bench,
        ),
        "创建机器人内参复标",
    )
    _approve(ctx, client, second_path, second, "机器人内参复标")

    superseded = expect("读取被替代的内参标定", client.call("GET", str(first_path)), 200)
    if not isinstance(superseded, dict):
        raise SeedFailure("读取被替代的内参标定：响应不是对象")
    if superseded.get("status") != "SUPERSEDED":
        raise SeedFailure(f"旧内参标定：期望 SUPERSEDED，实际 {superseded.get('status')}")


def _calibrate_device(
    ctx: Context, client: Client, base: str, device_id: str, profile: str
) -> None:
    record, path = _calibration(
        client,
        base,
        _calibration_body(
            profile,
            "DEVICE",
            device_id,
            "EXTRINSICS",
            "标定板基座-深度相机外参标定",
            _extrinsics([0.046, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]),
            frames=("camera_link", "camera_depth_optical_frame"),
            uncertainty={"translation_m": 0.0008, "rotation_rad": 0.0004},
            source="临港标定间-外参标定台",
        ),
        "创建相机模组外参标定",
    )
    _approve(ctx, client, path, record, "相机模组外参标定")


def _calibrate_sensor_intrinsics(
    ctx: Context, client: Client, base: str, sensor_id: str, profile: str
) -> dict[str, Any]:
    record, path = _calibration(
        client,
        base,
        _calibration_body(
            profile,
            "SENSOR",
            sensor_id,
            "INTRINSICS",
            "棋盘格张正友标定（9x6，2.5cm）",
            _intrinsics(903.61, 902.94, 638.72, 358.14),
            uncertainty={"reprojection_error_px": 0.35},
            source="临港标定间-双目标定台",
        ),
        "创建 RGB 传感器内参标定",
    )
    return _approve(ctx, client, path, record, "RGB 传感器内参标定")


def _calibrate_rejected(
    ctx: Context, client: Client, base: str, device_id: str, profile: str
) -> None:
    rejected, path = _calibration(
        client,
        base,
        _calibration_body(
            profile,
            "DEVICE",
            device_id,
            "EXTRINSICS",
            "夹爪相机外参标定（首轮）",
            _extrinsics([0.021, 0.012, 0.084], [0.7071, 0.0, 0.0, 0.7071]),
            frames=("tool0", "gripper_camera_frame"),
            uncertainty={"translation_m": 0.021, "rotation_rad": 0.018},
            source="临港标定间-手眼标定台",
        ),
        "创建夹爪相机外参标定（将被驳回）",
    )
    rejected = _patch(
        client,
        path,
        rejected["version"],
        {
            "parameters": _extrinsics([0.022, 0.011, 0.085], [0.7069, 0.0, 0.0, 0.7073]),
            "method": "夹爪相机外参标定（首轮，重新采集 15 组位姿）",
        },
        "修改夹爪相机外参草案",
    )
    if rejected.get("calibration_version") != 2:
        raise SeedFailure(
            "修改夹爪相机外参草案：期望 calibration_version=2，"
            f"实际 {rejected.get('calibration_version')}"
        )
    rejected = _act(
        client, path, "submit", rejected["version"], "夹爪相机外参标定：提交", keyed=False
    )
    rejected = _act(
        ctx.as_("reviewer"),
        path,
        "reject",
        rejected["version"],
        "夹爪相机外参标定：驳回",
        keyed=False,
    )
    if rejected.get("status") != "REJECTED":
        raise SeedFailure(f"夹爪相机外参标定：期望 REJECTED，实际 {rejected.get('status')}")


def _calibrate_time_offset(
    ctx: Context, client: Client, base: str, sensor_id: str, reference_id: str, profile: str
) -> None:
    record, path = _calibration(
        client,
        base,
        _calibration_body(
            profile,
            "SENSOR",
            sensor_id,
            "TIME_OFFSET",
            "硬件触发同源校时",
            {
                "sensor_id": sensor_id,
                "reference_sensor_id": reference_id,
                "offset_ns": "125000",
                "method": "HARDWARE_TRIGGER",
                "drift_ppm": 0.8,
            },
            uncertainty={"offset_ns": 3000},
            source="临港标定间-同步触发台",
        ),
        "创建深度传感器时间偏移标定",
    )
    record = _approve(ctx, client, path, record, "深度传感器时间偏移标定")
    record = _act(client, path, "revoke", record["version"], "作废时间偏移标定", keyed=False)
    if record.get("status") != "REVOKED":
        raise SeedFailure(f"深度传感器时间偏移标定：期望 REVOKED，实际 {record.get('status')}")


def _calibrate_others(
    ctx: Context,
    client: Client,
    base: str,
    devices: dict[str, dict[str, Any]],
    sensors: dict[str, dict[str, Any]],
    device_profile: str,
    sensor_profile: str,
) -> dict[str, Any]:
    _calibrate_device(ctx, client, base, str(devices["camera"]["device_id"]), device_profile)
    sensor_intrinsics = _calibrate_sensor_intrinsics(
        ctx, client, base, str(sensors["rgb"]["sensor_id"]), sensor_profile
    )
    _calibrate_rejected(ctx, client, base, str(devices["gripper"]["device_id"]), device_profile)
    _calibrate_time_offset(
        ctx,
        client,
        base,
        str(sensors["depth"]["sensor_id"]),
        str(sensors["rgb"]["sensor_id"]),
        sensor_profile,
    )
    return {"sensor_intrinsics": sensor_intrinsics}


# --- 生命周期动作 ----------------------------------------------------------


def _drive_robots(client: Client, base: str, robots: dict[str, dict[str, Any]]) -> None:
    path = f"{base}/robots/{robots['primary']['robot_id']}"
    _step(client, path, "activate", robots["primary"]["version"], "ACTIVE", "激活机器人 A1")

    path = f"{base}/robots/{robots['maintenance']['robot_id']}"
    item = _step(
        client, path, "activate", robots["maintenance"]["version"], "ACTIVE", "激活机器人 M2"
    )
    _step(
        client,
        path,
        "maintenance",
        item["version"],
        "IN_MAINTENANCE",
        "机器人 M2 进入维护",
        body={"reason": "驱动轮编码器异响，返修", "maintenance_record_id": str(uuid.uuid4())},
    )

    path = f"{base}/robots/{robots['retired']['robot_id']}"
    item = _step(client, path, "activate", robots["retired"]["version"], "ACTIVE", "激活机器人 S3")
    _step(
        client,
        path,
        "retire",
        item["version"],
        "RETIRED",
        "退役机器人 S3",
        body={"reason": "2 号关节间隙超差，退出采集任务"},
    )

    path = f"{base}/robots/{robots['faulty']['robot_id']}"
    item = _step(client, path, "activate", robots["faulty"]["version"], "ACTIVE", "激活机器人 A4")
    _step(
        client,
        path,
        "fault",
        item["version"],
        "FAULT",
        "上报机器人 A4 故障",
        body={
            "fault_code": "MOTOR_OVERTEMP",
            "fault_detail": "右臂肘关节电机温度 92℃，超过 85℃ 保护阈值",
            "occurred_at": "2026-11-17T02:15:00Z",
        },
    )


def _drive_devices(client: Client, base: str, devices: dict[str, dict[str, Any]]) -> None:
    path = f"{base}/devices/{devices['camera']['device_id']}"
    _step(client, path, "activate", devices["camera"]["version"], "ACTIVE", "启用相机模组")

    path = f"{base}/devices/{devices['gripper']['device_id']}"
    _step(client, path, "activate", devices["gripper"]["version"], "ACTIVE", "启用力控夹爪")

    path = f"{base}/devices/{devices['host']['device_id']}"
    item = _step(client, path, "activate", devices["host"]["version"], "ACTIVE", "启用工控主机")
    item = _step(
        client,
        path,
        "fault",
        item["version"],
        "FAULT",
        "上报工控主机故障",
        body={
            "fault_code": "PSU_OVERLOAD",
            "fault_detail": "24V 电源模块输出跌落至 19.4V，触发欠压保护",
            "occurred_at": "2026-11-17T03:40:00Z",
        },
    )
    item = _step(
        client,
        path,
        "maintenance",
        item["version"],
        "IN_MAINTENANCE",
        "工控主机进入维护",
        body={
            "reason": "更换 24V 600W 电源模块并复测满载电压",
            "maintenance_record_id": str(uuid.uuid4()),
        },
    )
    _step(
        client,
        path,
        "activate",
        item["version"],
        "ACTIVE",
        "工控主机复检后恢复",
        body={
            "reason": "满载 12 小时压测通过，输出 24.1V",
            "maintenance_record_id": str(uuid.uuid4()),
        },
    )

    path = f"{base}/devices/{devices['teleop']['device_id']}"
    item = _step(client, path, "activate", devices["teleop"]["version"], "ACTIVE", "启用遥操作主手")
    _step(
        client,
        path,
        "retire",
        item["version"],
        "RETIRED",
        "退役遥操作主手",
        body={"reason": "主手拇指按键磨损，由新型号替换"},
    )


def _drive_sensors(
    client: Client,
    base: str,
    sensors: dict[str, dict[str, Any]],
    sensor_intrinsics: dict[str, Any],
) -> None:
    path = f"{base}/sensors/{sensors['rgb']['sensor_id']}"
    rgb = _patch(
        client,
        path,
        sensors["rgb"]["version"],
        {"intrinsic_calibration_id": str(sensor_intrinsics["calibration_id"])},
        "回填 RGB 传感器内参标定",
    )
    _step(client, path, "activate", rgb["version"], "ACTIVE", "启用 RGB 传感器")

    path = f"{base}/sensors/{sensors['depth']['sensor_id']}"
    _step(client, path, "activate", sensors["depth"]["version"], "ACTIVE", "启用深度传感器")

    path = f"{base}/sensors/{sensors['joint']['sensor_id']}"
    item = _step(
        client, path, "activate", sensors["joint"]["version"], "ACTIVE", "启用关节状态传感器"
    )
    _step(
        client,
        path,
        "maintenance",
        item["version"],
        "IN_MAINTENANCE",
        "关节状态传感器进入维护",
        body={
            "reason": "左臂 7 号编码器信号抖动，重新压接排线",
            "maintenance_record_id": str(uuid.uuid4()),
        },
    )

    path = f"{base}/sensors/{sensors['imu']['sensor_id']}"
    item = _step(client, path, "activate", sensors["imu"]["version"], "ACTIVE", "启用 IMU 传感器")
    _step(
        client,
        path,
        "retire",
        item["version"],
        "RETIRED",
        "退役 IMU 传感器",
        body={"reason": "零偏漂移 0.08 rad/s，超出 0.02 rad/s 验收线"},
    )


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp

    robot_profile, device_profile, sensor_profile = _profiles(client, base)
    robots = _robots(client, base, stamp, robot_profile, owner=ctx.user_id)
    devices = _devices(
        client, base, stamp, str(robots["primary"]["robot_id"]), device_profile, owner=ctx.user_id
    )
    sensors = _sensors(
        client,
        base,
        stamp,
        str(devices["camera"]["device_id"]),
        str(robots["primary"]["robot_id"]),
        sensor_profile,
    )

    # 标定记录必须在目标实体之后创建：仓储按 (project_id, target_type, target_id)
    # 校验目标存在（backend/app/infrastructure/repositories/calibration_records.py）。
    _calibrate_robot(ctx, client, base, str(robots["primary"]["robot_id"]), robot_profile)
    extra = _calibrate_others(ctx, client, base, devices, sensors, device_profile, sensor_profile)

    _drive_robots(client, base, robots)
    _drive_devices(client, base, devices)
    _drive_sensors(client, base, sensors, extra["sensor_intrinsics"])

    ctx.remember(GROUP, "robot_primary", str(robots["primary"]["robot_id"]))
    ctx.remember(GROUP, "robot_maintenance", str(robots["maintenance"]["robot_id"]))
    ctx.remember(GROUP, "device_camera", str(devices["camera"]["device_id"]))
    ctx.remember(GROUP, "device_host", str(devices["host"]["device_id"]))
    ctx.remember(GROUP, "sensor_rgb", str(sensors["rgb"]["sensor_id"]))
