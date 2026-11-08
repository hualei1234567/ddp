"""DDP-205 前后端契约 fixture 独立探针。

用途：
1. 用真实 FastAPI 路由（真实 SQLite + 真实身份仓储 + 真实 DTO 序列化）
   生成设备/传感器/标定响应；
2. 与前端 DDP-206 契约 fixture 做字段集合与关键值类型对比。

数据来源：T2 合成数据，仅用于契约形状验证，不是业务验收数据。
本脚本不修改业务代码，只在内存临时库中运行。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

os.environ["DDP_ENV"] = "local"
os.environ.pop("DDP_LOCAL_HEADER_AUTH", None)

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.deps import get_current_principal  # noqa: E402
from app.domain.devices import (  # noqa: E402
    CalibrationProfile,
    CalibrationScope,
    CalibrationTargetType,
    CalibrationType,
)
from app.domain.identity.models import (  # noqa: E402
    PlatformRole,
    Principal,
    ProjectRole,
    ProjectScope,
)
from app.infrastructure.db.base import Base  # noqa: E402
from app.infrastructure.db.models.devices import CalibrationProfile as _ProfileModel  # noqa: E402,F401
from app.infrastructure.db.models.identity import Project, ProjectMember, User  # noqa: E402
from app.infrastructure.db.session import (  # noqa: E402
    create_db_engine,
    create_session_factory,
    get_session,
)
from app.infrastructure.repositories.devices import SqlCalibrationProfileRepository  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

FIXTURE = REPO_ROOT / "apps/web/src/services/__fixtures__/deviceContract.fixtures.ts"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def fixture_keys(text: str) -> dict[str, set[str]]:
    """提取 fixture 三个顶层对象的字段名（两空格缩进的 key）。"""
    blocks: dict[str, set[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"export const (backend\w+Fixture) = \{", line)
        if match:
            current = match.group(1)
            blocks[current] = set()
            continue
        if current and line == "};":
            current = None
            continue
        if current:
            key = re.match(r"  ([a-z_]+):", line)
            if key:
                blocks[current].add(key.group(1))
    return blocks


def is_uuid(value: object) -> bool:
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def main() -> int:
    text = FIXTURE.read_text(encoding="utf-8")
    blocks = fixture_keys(text)

    # 真实后端环境。
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = create_session_factory(engine)

    project_a = uuid4()
    editor = uuid4()
    owner = uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="contract-project", name="契约项目"),
                User(id=editor, display_name="编辑者"),
                User(id=owner, display_name="负责人"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project_a, user_id=editor, role=ProjectRole.EDITOR.value),
                ProjectMember(project_id=project_a, user_id=owner, role=ProjectRole.OWNER.value),
            ]
        )
        session.commit()

    app = create_app()

    def override_session():
        session = factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=editor,
        platform_role=PlatformRole.MEMBER,
    )
    client = TestClient(app)

    device_response = client.post(
        f"/api/v1/projects/{project_a}/devices",
        json={"code": "DEV-001", "name": "主控计算单元", "device_type": "COMPUTE"},
    )
    assert device_response.status_code == 201, device_response.text
    device = device_response.json()

    sensor_response = client.post(
        f"/api/v1/projects/{project_a}/sensors",
        json={
            "code": "CAM-001",
            "sensor_type": "RGB_D",
            "sample_rate_hz": 30,
            "coordinate_frame": "camera/optical_frame",
            "device_id": device["device_id"],
            "channels": 4,
            "sync_mode": "NONE",
        },
    )
    assert sensor_response.status_code == 201, sensor_response.text
    sensor = sensor_response.json()

    profile = CalibrationProfile(
        profile_id=uuid4(),
        project_id=project_a,
        name="DEVICE 默认标定配置",
        scope=CalibrationScope.DEVICE,
        target_type=CalibrationTargetType.DEVICE,
        version=1,
        schema_version="ddp.device.calibration-parameters/1.0.0",
        required_types=(CalibrationType.INTRINSICS,),
    )
    with factory() as session:
        SqlCalibrationProfileRepository(session).add(
            ProjectScope(project_a, actor_id=owner),
            profile,
        )
        session.commit()

    calibration_response = client.post(
        f"/api/v1/projects/{project_a}/calibrations",
        json={
            "profile_id": str(profile.profile_id),
            "target_type": "DEVICE",
            "target_id": device["device_id"],
            "calibration_type": "EXTRINSICS",
            "method": "hand-eye-tool/1.0",
            "parameters": {
                "translation_m": [0.1, 0.2, 0.3],
                "rotation_quat_wxyz": [1.0, 0.0, 0.0, 0.0],
                "transform_type": "eye_in_hand",
            },
            "valid_from": "2026-01-01T00:00:00Z",
            "parent_frame": "robot/base_link",
            "child_frame": "camera/optical_frame",
            "evidence_asset_ids": [str(uuid4())],
            "uncertainty": {"translation_m": 0.001, "rotation_rad": 0.0001},
        },
    )
    assert calibration_response.status_code == 201, calibration_response.text
    calibration = calibration_response.json()

    # 字段集合对比（后端字段集合必须覆盖 fixture 的字段）。
    for name, payload, fixture_name in (
        ("device", device, "backendDeviceFixture"),
        ("sensor", sensor, "backendSensorFixture"),
        ("calibration", calibration, "backendCalibrationFixture"),
    ):
        backend_keys = set(payload)
        fixture_set = blocks.get(fixture_name, set())
        missing = fixture_set - backend_keys
        extra = backend_keys - fixture_set
        record(
            f"{name} 字段集合",
            "PASS" if not missing else "FAIL",
            f"fixture={len(fixture_set)}, backend={len(backend_keys)}, "
            f"missing_in_backend={sorted(missing)}, extra_in_backend={sorted(extra)}",
        )

    # 关键类型/值对齐。
    def has(fragment: str) -> bool:
        return fragment in text

    fixture_scope_lower = has('scope: "device",')
    fixture_extrinsic = has('calibration_type: "EXTRINSIC",')
    fixture_sync_hardware = has('sync_mode: "hardware",')
    fixture_profile_1 = has('profile_id: "profile-1",')
    fixture_user_1 = has('performed_by: "user-1",')
    fixture_checksum_abc = has('checksum: "sha256:abc",')
    fixture_asset_1 = has('evidence_asset_ids: ["asset-1"],')

    checks: list[tuple[str, bool, str]] = [
        (
            "channels 为整数",
            isinstance(sensor["channels"], int) and has("channels: 4,"),
            f"backend={sensor['channels']!r}",
        ),
        (
            "uncertainty 为对象",
            isinstance(calibration["uncertainty"], dict)
            and has("uncertainty: { translation_m: 0.001, rotation_rad: 0.002 },"),
            f"backend={calibration['uncertainty']!r}",
        ),
        (
            "sensor_type 使用 RGB_D",
            sensor["sensor_type"] == "RGB_D" and has('sensor_type: "RGB_D",'),
            f"backend={sensor['sensor_type']!r}",
        ),
        (
            "scope 值大小写",
            calibration["scope"] == "DEVICE" and not fixture_scope_lower,
            f"backend={calibration['scope']!r}, fixture_has_lowercase={fixture_scope_lower}",
        ),
        (
            "calibration_type 枚举",
            calibration["calibration_type"] == "EXTRINSICS" and not fixture_extrinsic,
            f"backend={calibration['calibration_type']!r}, "
            f"fixture_has_EXTRINSIC={fixture_extrinsic}",
        ),
        (
            "sync_mode 枚举",
            sensor["sync_mode"]
            in {"PTP", "NTP", "HARDWARE_TRIGGER", "SOFTWARE_ESTIMATE", "MANUAL", "NONE"}
            and not fixture_sync_hardware,
            f"backend={sensor['sync_mode']!r}, fixture_has_hardware={fixture_sync_hardware}",
        ),
        (
            "profile_id 为 UUID",
            is_uuid(calibration["profile_id"]) and not fixture_profile_1,
            f"backend={calibration['profile_id']!r}, fixture_has_profile_1={fixture_profile_1}",
        ),
        (
            "performed_by 为 UUID",
            is_uuid(calibration["performed_by"]) and not fixture_user_1,
            f"backend={calibration['performed_by']!r}, fixture_has_user_1={fixture_user_1}",
        ),
        (
            "checksum 为 sha256:<64hex>",
            re.fullmatch(r"sha256:[0-9a-f]{64}", str(calibration["checksum"])) is not None
            and not fixture_checksum_abc,
            f"backend={calibration['checksum']!r}, fixture_has_abc={fixture_checksum_abc}",
        ),
        (
            "evidence_asset_ids 为 UUID 数组",
            all(is_uuid(item) for item in calibration["evidence_asset_ids"])
            and not fixture_asset_1,
            f"backend={calibration['evidence_asset_ids']!r}, "
            f"fixture_has_asset_1={fixture_asset_1}",
        ),
    ]
    for name, ok, detail in checks:
        record(name, "PASS" if ok else "FAIL", detail)

    engine.dispose()
    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
