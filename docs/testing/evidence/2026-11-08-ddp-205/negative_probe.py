"""DDP-205 设备域 API 负向用例独立探针。

覆盖任务卡点名的负向场景中可经真实 HTTP 层验证的部分：
- 客户端伪造 performed_by / validated_by；
- 跨项目 target_id；
- viewer 写操作；
- 超长字段；
- NaN 数值；
- 客户端 PATCH status。

数据来源：T2 合成数据；真实 FastAPI + 真实 SQLite 仓储，不是 Mock。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

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

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}: {detail}")


def main() -> int:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = create_session_factory(engine)

    project_a, project_b = uuid4(), uuid4()
    editor, owner, viewer, owner_b = uuid4(), uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="neg-a", name="项目 A"),
                Project(id=project_b, code="neg-b", name="项目 B"),
                User(id=editor, display_name="编辑者"),
                User(id=owner, display_name="负责人"),
                User(id=viewer, display_name="只读用户"),
                User(id=owner_b, display_name="项目 B 负责人"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project_a, user_id=editor, role=ProjectRole.EDITOR.value),
                ProjectMember(project_id=project_a, user_id=owner, role=ProjectRole.OWNER.value),
                ProjectMember(project_id=project_a, user_id=viewer, role=ProjectRole.VIEWER.value),
                ProjectMember(project_id=project_b, user_id=owner_b, role=ProjectRole.OWNER.value),
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
    current = {"user": editor}
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=current["user"],
        platform_role=PlatformRole.MEMBER,
    )

    def client_for(user):
        current["user"] = user
        return TestClient(app)

    def create_device(user, code: str = "device-1", project=project_a):
        response = client_for(user).post(
            f"/api/v1/projects/{project}/devices",
            json={"code": code, "name": f"设备 {code}", "device_type": "COMPUTE"},
        )
        return response

    def seed_profile() -> CalibrationProfile:
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
        return profile

    def calibration_payload(profile_id, target_id) -> dict[str, object]:
        return {
            "profile_id": str(profile_id),
            "target_type": "DEVICE",
            "target_id": str(target_id),
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
        }

    device_created = create_device(editor)
    assert device_created.status_code == 201, device_created.text
    device_id = device_created.json()["device_id"]
    profile = seed_profile()

    # 1. 伪造 performed_by：请求模型 extra=forbid，必须 422。
    forged_payload = calibration_payload(profile.profile_id, device_id)
    forged_payload["performed_by"] = str(uuid4())
    forged = client_for(editor).post(
        f"/api/v1/projects/{project_a}/calibrations",
        json=forged_payload,
    )
    record(
        "伪造 performed_by",
        "PASS"
        if forged.status_code == 422 and forged.json()["error"]["code"] == "validation_failed"
        else "FAIL",
        f"status={forged.status_code}, body={forged.json()}",
    )

    created = client_for(editor).post(
        f"/api/v1/projects/{project_a}/calibrations",
        json=calibration_payload(profile.profile_id, device_id),
    )
    assert created.status_code == 201, created.text
    calibration_id = created.json()["calibration_id"]

    # 2. 伪造 validated_by：PATCH 模型 extra=forbid，必须 422。
    forged_validator = client_for(owner).patch(
        f"/api/v1/projects/{project_a}/calibrations/{calibration_id}",
        headers={"If-Match": '"1"'},
        json={"validated_by": str(owner)},
    )
    record(
        "伪造 validated_by",
        "PASS"
        if forged_validator.status_code == 422
        and forged_validator.json()["error"]["code"] == "validation_failed"
        else "FAIL",
        f"status={forged_validator.status_code}, body={forged_validator.json()}",
    )

    # 3. 跨项目 target_id：项目 B 的设备作为项目 A 标定目标，必须 422 目标不可用。
    foreign_device = create_device(owner_b, code="b-device", project=project_b)
    assert foreign_device.status_code == 201, foreign_device.text
    cross_target = client_for(editor).post(
        f"/api/v1/projects/{project_a}/calibrations",
        json=calibration_payload(profile.profile_id, foreign_device.json()["device_id"]),
    )
    record(
        "跨项目 target_id",
        "PASS"
        if cross_target.status_code == 422
        and cross_target.json()["error"]["code"] == "calibration_target_invalid"
        else "FAIL",
        f"status={cross_target.status_code}, body={cross_target.json()}",
    )

    # 4. viewer 写：创建设备必须 403。
    viewer_write = create_device(viewer, code="viewer-device")
    record(
        "viewer 写设备",
        "PASS"
        if viewer_write.status_code == 403
        and viewer_write.json()["error"]["code"] == "forbidden"
        else "FAIL",
        f"status={viewer_write.status_code}, body={viewer_write.json()}",
    )

    # 5. 超长字段：code 65 字符必须 422。
    overlong = create_device(editor, code="c" * 65)
    record(
        "超长 code",
        "PASS"
        if overlong.status_code == 422 and overlong.json()["error"]["code"] == "validation_failed"
        else "FAIL",
        f"status={overlong.status_code}, body={overlong.json()}",
    )

    # 6. NaN：sample_rate_hz 传非标准 JSON NaN，必须 422。
    nan_body = (
        '{"code":"nan-sensor","sensor_type":"IMU","sample_rate_hz":NaN,'
        f'"coordinate_frame":"imu_link","device_id":"{device_id}"}}'
    )
    nan_response = client_for(editor).post(
        f"/api/v1/projects/{project_a}/sensors",
        content=nan_body,
        headers={"Content-Type": "application/json"},
    )
    record(
        "NaN sample_rate_hz",
        "PASS"
        if nan_response.status_code == 422
        and nan_response.json()["error"]["code"] == "validation_failed"
        else "FAIL",
        f"status={nan_response.status_code}, body={nan_response.json()}",
    )

    # 7. 客户端 PATCH status：必须 422。
    status_patch = client_for(editor).patch(
        f"/api/v1/projects/{project_a}/devices/{device_id}",
        headers={"If-Match": '"1"'},
        json={"status": "ACTIVE"},
    )
    record(
        "PATCH status",
        "PASS"
        if status_patch.status_code == 422
        and status_patch.json()["error"]["code"] == "validation_failed"
        else "FAIL",
        f"status={status_patch.status_code}, body={status_patch.json()}",
    )

    # 8. viewer 删除语义：当前设备 API 未提供 DELETE 端点，记录为 NOT RUN 事实。
    delete_probe = client_for(owner).delete(
        f"/api/v1/projects/{project_a}/devices/{device_id}",
    )
    record(
        "设备 DELETE 端点",
        "NOT RUN",
        f"未实现设备 DELETE 路由，方法返回 status={delete_probe.status_code}；"
        "viewer 删除仅由依赖层角色测试覆盖",
    )

    engine.dispose()
    print("\n=== 汇总 ===")
    for name, status, _detail in RESULTS:
        print(f"{status}\t{name}")
    failures = [item for item in RESULTS if item[1] == "FAIL"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
