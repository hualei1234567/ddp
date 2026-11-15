"""用真实 FastAPI 响应生成前端设备契约快照。

用途：
- ``--write``：调用真实 FastAPI 路由（真实 SQLite + 真实身份仓储 + 真实 DTO
  序列化），生成 ``src/services/__fixtures__/deviceContract.snapshot.json``；
- ``--check``：重新生成并逐值比较已提交快照，后端字段或序列化漂移时以非 0
  退出码失败。

数据来源：T2 合成数据，仅用于契约形状验证，不是业务验收数据；所有 UUID、
标定配置与证据 ID 都是固定的合成值，保证快照可重复生成。
本脚本不修改业务代码，只在内存临时库中运行。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

SCRIPT_PATH = Path(__file__).resolve()
WEB_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
BACKEND_PYTHON = BACKEND_DIR / ".venv" / "bin" / "python"
SNAPSHOT_PATH = (
    WEB_ROOT / "src" / "services" / "__fixtures__" / "deviceContract.snapshot.json"
)

# 固定合成标识：让真实响应快照可重复生成，避免每次运行因随机 UUID 产生噪声。
PROJECT_ID = UUID("11111111-1111-7111-8111-111111111111")
EDITOR_ID = UUID("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa")
OWNER_ID = UUID("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb")
PROFILE_ID = UUID("cccccccc-cccc-7ccc-8ccc-cccccccccccc")
EVIDENCE_ID = UUID("dddddddd-dddd-7ddd-8ddd-dddddddddddd")
DEVICE_ID = UUID("22222222-2222-7222-8222-222222222222")
SENSOR_ID = UUID("33333333-3333-7333-8333-333333333333")
CALIBRATION_ID = UUID("44444444-4444-7444-8444-444444444444")
ROBOT_ID = UUID("55555555-5555-7555-8555-555555555555")


def _reexec_with_backend_python() -> None:
    """当前解释器缺少后端依赖时，切换到 backend/.venv。"""
    if os.environ.get("DDP_CONTRACT_NO_REEXEC") == "1":
        return
    if not BACKEND_PYTHON.exists():
        return
    # 不能比较 resolve()：venv 的 python 通常符号链接到系统解释器，
    # 两者 resolve 后会相同，导致误判“已在 venv 中”而不切换。
    if Path(sys.executable).parent == BACKEND_PYTHON.parent:
        return
    os.execv(
        str(BACKEND_PYTHON),
        [str(BACKEND_PYTHON), str(SCRIPT_PATH), *sys.argv[1:]],
    )


def _load_backend():
    os.environ["DDP_ENV"] = "local"
    os.environ.pop("DDP_LOCAL_HEADER_AUTH", None)
    os.environ["DDP_CONTRACT_NO_REEXEC"] = "1"
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    from app.api.deps import get_current_principal
    from app.application.devices import calibrations as calibrations_module
    from app.application.devices import devices as devices_module
    from app.application.devices import robots as robots_module
    from app.application.devices import sensors as sensors_module
    from app.domain.devices import (
        CalibrationProfile,
        CalibrationScope,
        CalibrationTargetType,
        CalibrationType,
    )
    from app.domain.identity.models import (
        PlatformRole,
        Principal,
        ProjectRole,
        ProjectScope,
    )
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.models.devices import (  # noqa: F401
        CalibrationProfile as _ProfileModel,
    )
    from app.infrastructure.db.models.identity import Project, ProjectMember, User
    from app.infrastructure.db.session import (
        create_db_engine,
        create_session_factory,
        get_session,
    )
    from app.infrastructure.repositories.devices import SqlCalibrationProfileRepository
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session, sessionmaker

    return {
        "get_current_principal": get_current_principal,
        "calibrations_module": calibrations_module,
        "robots_module": robots_module,
        "devices_module": devices_module,
        "sensors_module": sensors_module,
        "CalibrationProfile": CalibrationProfile,
        "CalibrationScope": CalibrationScope,
        "CalibrationTargetType": CalibrationTargetType,
        "CalibrationType": CalibrationType,
        "PlatformRole": PlatformRole,
        "Principal": Principal,
        "ProjectRole": ProjectRole,
        "ProjectScope": ProjectScope,
        "Base": Base,
        "Project": Project,
        "ProjectMember": ProjectMember,
        "User": User,
        "create_db_engine": create_db_engine,
        "create_session_factory": create_session_factory,
        "get_session": get_session,
        "SqlCalibrationProfileRepository": SqlCalibrationProfileRepository,
        "create_app": create_app,
        "TestClient": TestClient,
        "Session": Session,
        "sessionmaker": sessionmaker,
    }


def _patch_deterministic_ids(backend) -> None:
    """按调用顺序替换应用层的 ``new_id``，使响应中的服务端生成 ID 可复现。

    G-18 之后设备域用 UUIDv7 生成 ID（``app.domain.common.ids.new_id``），
    所以固定点也落在 ``new_id`` 上，而不是各模块里已不存在的 ``uuid4``。
    """
    generated = iter((DEVICE_ID, SENSOR_ID, CALIBRATION_ID, ROBOT_ID))

    def fixed_new_id() -> UUID:
        return next(generated)

    for key in (
        "devices_module",
        "sensors_module",
        "calibrations_module",
        "robots_module",
    ):
        backend[key].new_id = fixed_new_id


def build_snapshot() -> dict[str, object]:
    _reexec_with_backend_python()
    backend = _load_backend()
    _patch_deterministic_ids(backend)

    engine = backend["create_db_engine"](
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    backend["Base"].metadata.create_all(engine)
    factory = backend["create_session_factory"](engine)

    with factory() as session:
        session.add_all(
            [
                backend["Project"](
                    id=PROJECT_ID,
                    code="contract-project",
                    name="契约项目",
                ),
                backend["User"](id=EDITOR_ID, display_name="编辑者"),
                backend["User"](id=OWNER_ID, display_name="负责人"),
            ]
        )
        session.flush()
        session.add_all(
            [
                backend["ProjectMember"](
                    project_id=PROJECT_ID,
                    user_id=EDITOR_ID,
                    role=backend["ProjectRole"].EDITOR.value,
                ),
                backend["ProjectMember"](
                    project_id=PROJECT_ID,
                    user_id=OWNER_ID,
                    role=backend["ProjectRole"].OWNER.value,
                ),
            ]
        )
        session.commit()

    app = backend["create_app"]()

    def override_session():
        session = factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[backend["get_session"]] = override_session
    app.dependency_overrides[backend["get_current_principal"]] = lambda: backend[
        "Principal"
    ](
        user_id=EDITOR_ID,
        platform_role=backend["PlatformRole"].MEMBER,
    )
    client = backend["TestClient"](app)

    profile = backend["CalibrationProfile"](
        profile_id=PROFILE_ID,
        project_id=PROJECT_ID,
        name="DEVICE 默认标定配置",
        scope=backend["CalibrationScope"].DEVICE,
        target_type=backend["CalibrationTargetType"].DEVICE,
        profile_version=1,
        schema_version="ddp.device.calibration-parameters/1.0.0",
        required_types=(backend["CalibrationType"].INTRINSICS,),
    )
    with factory() as session:
        backend["SqlCalibrationProfileRepository"](session).add(
            backend["ProjectScope"](PROJECT_ID, actor_id=OWNER_ID),
            profile,
        )
        session.commit()

    device_response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/devices",
        json={
            "code": "DEV-001",
            "name": "主控计算单元",
            "device_type": "COMPUTE",
            "vendor": "Acme",
            "model": "X1",
            "serial_number": "SN-001",
            "firmware_version": "1.0.0",
            "hardware_version": "rev-a",
            "capability_profile": {"dof": 7, "interfaces": ["can"]},
        },
    )
    assert device_response.status_code == 201, device_response.text
    device = device_response.json()

    sensor_response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/sensors",
        json={
            "code": "CAM-001",
            "sensor_type": "RGB_D",
            "sample_rate_hz": 30,
            "coordinate_frame": "camera/optical_frame",
            "device_id": device["device_id"],
            "vendor": "Acme",
            "model": "DepthCam",
            "serial_number": "SN-CAM-1",
            "resolution_w": 1920,
            "resolution_h": 1080,
            "bit_depth": 8,
            "channels": 4,
            "sync_mode": "HARDWARE_TRIGGER",
            "sync_group": "rig-a",
            "mount_position": "front",
        },
    )
    assert sensor_response.status_code == 201, sensor_response.text
    sensor = sensor_response.json()

    calibration_response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/calibrations",
        json={
            "profile_id": str(PROFILE_ID),
            "target_type": "DEVICE",
            "target_id": device["device_id"],
            "calibration_type": "EXTRINSICS",
            "method": "hand-eye-tool/1.0",
            "parameters": {
                "translation_m": [0.1, 0.2, 0.3],
                "rotation_quat_wxyz": [1.0, 0.0, 0.0, 0.0],
                "transform_type": "STATIC",
            },
            "valid_from": "2026-01-01T00:00:00Z",
            "parent_frame": "robot/base_link",
            "child_frame": "camera/optical_frame",
            "evidence_asset_ids": [str(EVIDENCE_ID)],
            "uncertainty": {"translation_m": 0.001, "rotation_rad": 0.0001},
        },
    )
    assert calibration_response.status_code == 201, calibration_response.text
    calibration = calibration_response.json()

    sensor_page_response = client.get(
        f"/api/v1/projects/{PROJECT_ID}/sensors",
        params={"device_id": device["device_id"]},
    )
    assert sensor_page_response.status_code == 200, sensor_page_response.text

    calibration_page_response = client.get(
        f"/api/v1/projects/{PROJECT_ID}/calibrations",
        params={"target_type": "DEVICE", "target_id": device["device_id"]},
    )
    assert calibration_page_response.status_code == 200, (
        calibration_page_response.text
    )

    # G-01：Robot 与 CalibrationProfile 路由。这里走真实 HTTP 路由，
    # 因此快照同时证明"路由存在""响应字段与前端类型一致"两件事。
    robot_response = client.post(
        f"/api/v1/projects/{PROJECT_ID}/robots",
        json={
            "code": "ROB-001",
            "name": "研究型双臂机器人",
            "robot_type": "HUMANOID",
            "vendor": "Acme",
            "model": "R2",
            "serial_number": "SN-R-001",
        },
    )
    assert robot_response.status_code == 201, robot_response.text
    robot = robot_response.json()
    assert robot["robot_id"] == str(ROBOT_ID), robot["robot_id"]

    robot_page_response = client.get(f"/api/v1/projects/{PROJECT_ID}/robots")
    assert robot_page_response.status_code == 200, robot_page_response.text

    profile_response = client.get(
        f"/api/v1/projects/{PROJECT_ID}/calibration-profiles/{PROFILE_ID}"
    )
    assert profile_response.status_code == 200, profile_response.text

    snapshot = {
        "device": device,
        "sensor": sensor,
        "calibration": calibration,
        "robot": robot,
        "calibration_profile": profile_response.json(),
        "sensor_page": sensor_page_response.json(),
        "calibration_page": calibration_page_response.json(),
        "robot_page": robot_page_response.json(),
    }
    engine.dispose()
    return snapshot


def _field_diff(expected: object, actual: object, path: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        diffs: list[str] = []
        expected_keys = set(expected)
        actual_keys = set(actual)
        for key in sorted(expected_keys - actual_keys):
            diffs.append(f"{path}.{key} 仅存在于快照")
        for key in sorted(actual_keys - expected_keys):
            diffs.append(f"{path}.{key} 仅存在于后端响应")
        for key in sorted(expected_keys & actual_keys):
            diffs.extend(_field_diff(expected[key], actual[key], f"{path}.{key}"))
        return diffs
    if isinstance(expected, list) and isinstance(actual, list):
        diffs = []
        if len(expected) != len(actual):
            diffs.append(f"{path} 数组长度不同：快照={len(expected)}，后端={len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            diffs.extend(_field_diff(left, right, f"{path}[{index}]"))
        return diffs
    if expected != actual:
        return [f"{path} 值不同：快照={expected!r}，后端={actual!r}"]
    return []


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--check"
    if mode not in {"--check", "--write"}:
        print(f"未知参数：{mode}；仅支持 --check / --write", file=sys.stderr)
        return 2

    generated = build_snapshot()
    if mode == "--write":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(generated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"已写入真实响应快照：{SNAPSHOT_PATH.relative_to(REPO_ROOT)}")
        return 0

    if not SNAPSHOT_PATH.exists():
        print(f"快照不存在：{SNAPSHOT_PATH}", file=sys.stderr)
        print("请先运行：python3 scripts/device_contract_snapshot.py --write", file=sys.stderr)
        return 1

    committed = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    diffs = _field_diff(committed, generated)
    if diffs:
        print("后端设备契约与已提交快照不一致：", file=sys.stderr)
        for line in diffs:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("后端设备契约与已提交快照一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
