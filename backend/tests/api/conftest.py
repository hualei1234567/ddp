"""设备 API 测试夹具：真实 SQLite + 真实身份仓储 + 可替换认证主体。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
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


@dataclass(frozen=True)
class ApiIds:
    project_a: UUID
    project_b: UUID
    editor_a: UUID
    owner_a: UUID
    viewer_a: UUID
    outsider: UUID


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
    return TestClient(app)


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
