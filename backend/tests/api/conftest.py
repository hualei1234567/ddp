"""设备 API 测试夹具：真实 SQLite + 真实身份仓储 + 可替换认证主体。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from app import main as main_module
from app.api.deps import get_current_principal
from app.api.v1.upload_dependencies import get_object_storage
from app.core.config import Settings, get_settings
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
from app.infrastructure.db.models import devices as _devices  # noqa: F401
from app.infrastructure.db.models.identity import (
    Project,
    ProjectMember,
    User,
)
from app.infrastructure.db.session import create_db_engine, create_session_factory, get_session
from app.infrastructure.repositories.devices import SqlCalibrationProfileRepository
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.support_clients import managed_client


@dataclass(frozen=True)
class ApiIds:
    project_a: UUID
    project_b: UUID
    editor_a: UUID
    owner_a: UUID
    viewer_a: UUID
    outsider: UUID


def seed_identity(factory: sessionmaker[Session]) -> ApiIds:
    """写入两个项目、四个用户与成员关系；返回固定 ID 集合。

    抽成公开函数是为了让 ``tests/e2e`` 复用同一套身份基线：端到端链路如果
    自己再拼一份"项目 + 成员"，两边一旦分叉（角色或成员资格不同），隔离与
    权限结论就不可比。
    """
    project_a, project_b = uuid4(), uuid4()
    editor_a, owner_a, viewer_a, outsider = uuid4(), uuid4(), uuid4(), uuid4()
    ids = ApiIds(project_a, project_b, editor_a, owner_a, viewer_a, outsider)

    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=editor_a, display_name="编辑者"),
                User(id=owner_a, display_name="负责人"),
                User(id=viewer_a, display_name="只读用户"),
                User(id=outsider, display_name="项目外用户"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    project_id=project_a,
                    user_id=editor_a,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=owner_a,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=viewer_a,
                    role=ProjectRole.VIEWER.value,
                ),
                ProjectMember(
                    project_id=project_b,
                    user_id=owner_a,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        session.commit()
    return ids


@pytest.fixture()
def api_db() -> Iterator[tuple[sessionmaker[Session], ApiIds]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = seed_identity(factory)

    try:
        yield factory, ids
    finally:
        engine.dispose()


def make_client(
    factory: sessionmaker[Session],
    user_id: UUID,
    *,
    platform_role: PlatformRole = PlatformRole.MEMBER,
    object_storage: object | None = None,
    settings_override: Settings | None = None,
) -> TestClient:
    app = create_app()

    def override_session() -> Iterator[Session]:
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
        user_id=user_id,
        platform_role=platform_role,
    )
    if object_storage is not None:
        app.dependency_overrides[get_object_storage] = lambda: object_storage
    if settings_override is not None:
        app.dependency_overrides[get_settings] = lambda: settings_override
    return managed_client(app)


def device_payload(code: str = "device-1") -> dict[str, object]:
    return {
        "code": code,
        "name": f"设备 {code}",
        "device_type": "COMPUTE",
    }


def sensor_payload(
    *,
    code: str = "sensor-1",
    device_id: str,
    sensor_type: str = "RGB",
) -> dict[str, object]:
    return {
        "code": code,
        "sensor_type": sensor_type,
        "sample_rate_hz": 30.0,
        "coordinate_frame": "camera_link",
        "device_id": device_id,
    }


def seed_profile(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    target_type: str = "DEVICE",
) -> CalibrationProfile:
    target = CalibrationTargetType(target_type)
    profile = CalibrationProfile(
        profile_id=uuid4(),
        project_id=project_id,
        name=f"{target_type} 默认标定配置",
        scope=CalibrationScope(target.value),
        target_type=target,
        profile_version=1,
        schema_version="ddp.device.calibration-parameters/1.0.0",
        required_types=(CalibrationType.INTRINSICS,),
    )
    with factory() as session:
        SqlCalibrationProfileRepository(session).add(
            ProjectScope(project_id, actor_id=actor_id),
            profile,
        )
        session.commit()
    return profile


def calibration_payload(
    *,
    profile_id: str,
    target_id: str,
    evidence_asset_id: str,
    target_type: str = "DEVICE",
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "target_type": target_type,
        "target_id": target_id,
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
        "evidence_asset_ids": [evidence_asset_id],
        "uncertainty": {"translation_m": 0.001, "rotation_rad": 0.0001},
    }


# ---------------------------------------------------------------------------
# 指标测试夹具（/metrics 采集正确性 与 访问控制 两组用例共用）
#
# 放在 conftest 而不是测试文件里：两组用例需要同一个「真实应用 + 真实 SQLite +
# 可替换认证主体」装配，复制一份会让 create_app 的装配细节分叉——那条路径最容易
# 随框架升级悄悄变化。
# ---------------------------------------------------------------------------


@pytest.fixture()
def metrics_app(
    monkeypatch: pytest.MonkeyPatch,
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> Iterator[tuple[TestClient, ApiIds]]:
    """真实应用 + 真实 SQLite：业务指标采集走测试库，不打真实 PostgreSQL。

    认证主体替换为项目 A 的编辑者：这样路由能真正走到用例层，才能验证
    「同一路由模板 + 不同 UUID → 一条时间序列」而不是被认证层挡住。
    """
    factory, ids = api_db
    # ``create_app`` 里业务采集器在调用时才解析该名字，替换即生效。
    monkeypatch.setattr(main_module, "get_session_factory", lambda: factory)
    app = create_app()

    def override_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        user_id=ids.editor_a,
        platform_role=PlatformRole.MEMBER,
    )
    yield managed_client(app), ids


@pytest.fixture()
def metrics_client(metrics_app: tuple[TestClient, ApiIds]) -> TestClient:
    return metrics_app[0]
