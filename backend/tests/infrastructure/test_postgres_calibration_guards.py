"""PostgreSQL 兜底集成测试：复合外键与已校验标定不可变触发器。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过（BLOCKED），
与现有 PostgreSQL 项目隔离测试保持一致。测试在随机 schema 中建表，
``Base.metadata.create_all`` 的 ``after_create`` 会安装与 Alembic 0004 相同的
触发器 DDL。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.devices import (
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    Robot,
    Sensor,
    SensorType,
)
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.devices import (
    CalibrationRecord as CalibrationRecordModel,
)
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.devices import (
    SqlCalibrationRecordRepository,
    SqlRobotRepository,
    SqlSensorRepository,
)
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.infrastructure.calibration_support import (
    NOW,
    VALIDATED_AT,
    add_profile,
    draft_record,
    seed_validated,
)
from tests.infrastructure.calibration_support import (
    profile as make_profile,
)

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)


@pytest.fixture()
def postgres_env() -> Iterator[tuple[Engine, sessionmaker[Session]]]:
    schema = f"ddp_guard_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        yield engine, create_session_factory(engine)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _seed_project(
    factory: sessionmaker[Session],
    *,
    project_id,
    performer_id,
    validator_id,
) -> tuple[ProjectScope, Robot, Sensor]:
    scope = ProjectScope(project_id, actor_id=performer_id)
    robot = Robot(
        robot_id=uuid4(),
        project_id=project_id,
        code=f"robot-{project_id.hex[:8]}",
        name="测试机器人",
        robot_type="HUMANOID",
    )
    sensor = Sensor(
        sensor_id=uuid4(),
        project_id=project_id,
        code=f"sensor-{project_id.hex[:8]}",
        sensor_type=SensorType.RGB,
        sample_rate_hz=30.0,
        coordinate_frame="camera_link",
        robot_id=robot.robot_id,
    )
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="项目"),
                User(id=performer_id, display_name="执行人"),
                User(id=validator_id, display_name="校验人"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    id=uuid4(),
                    project_id=project_id,
                    user_id=performer_id,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    id=uuid4(),
                    project_id=project_id,
                    user_id=validator_id,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        session.flush()
        SqlRobotRepository(session).add(scope, robot)
        SqlSensorRepository(session).add(scope, sensor)
        session.commit()
    return scope, robot, sensor


def _direct_record(
    *,
    project_id,
    profile_id,
    target_id,
    performer_id,
    validator_id,
    status: CalibrationStatus,
    checksum: str,
) -> CalibrationRecordModel:
    return CalibrationRecordModel(
        id=uuid4(),
        project_id=project_id,
        profile_id=profile_id,
        scope=CalibrationScope.SENSOR.value,
        target_type=CalibrationTargetType.SENSOR.value,
        target_id=target_id,
        calibration_type=CalibrationType.INTRINSICS.value,
        method="postgres-direct",
        parameters={},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        status=status.value,
        evidence_asset_ids=[str(uuid4())],
        checksum=checksum,
        uncertainty={},
        performed_by=performer_id,
        validated_by=validator_id if status is not CalibrationStatus.DRAFT else None,
        validated_at=(
            datetime(2026, 1, 2, tzinfo=UTC) if status is not CalibrationStatus.DRAFT else None
        ),
        created_by=performer_id,
        updated_by=performer_id,
    )


def test_postgres_rejects_unknown_and_cross_project_target(
    postgres_env: tuple[Engine, sessionmaker[Session]],
) -> None:
    _, factory = postgres_env
    project_a, project_b = uuid4(), uuid4()
    performer_a, validator_a = uuid4(), uuid4()
    performer_b, validator_b = uuid4(), uuid4()
    scope_a, _, sensor_a = _seed_project(
        factory,
        project_id=project_a,
        performer_id=performer_a,
        validator_id=validator_a,
    )
    _, _, sensor_b = _seed_project(
        factory,
        project_id=project_b,
        performer_id=performer_b,
        validator_id=validator_b,
    )
    profile = make_profile(project_a)
    add_profile(factory, scope_a, profile)

    with factory() as session:
        session.add(
            _direct_record(
                project_id=project_a,
                profile_id=profile.profile_id,
                target_id=uuid4(),
                performer_id=performer_a,
                validator_id=validator_a,
                status=CalibrationStatus.DRAFT,
                checksum="sha256:" + "0" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with factory() as session:
        session.add(
            _direct_record(
                project_id=project_a,
                profile_id=profile.profile_id,
                target_id=sensor_b.sensor_id,
                performer_id=performer_a,
                validator_id=validator_a,
                status=CalibrationStatus.DRAFT,
                checksum="sha256:" + "0" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # 正向对照：同项目目标可以写入。
    with factory() as session:
        session.add(
            _direct_record(
                project_id=project_a,
                profile_id=profile.profile_id,
                target_id=sensor_a.sensor_id,
                performer_id=performer_a,
                validator_id=validator_a,
                status=CalibrationStatus.DRAFT,
                checksum="sha256:" + "0" * 64,
            )
        )
        session.commit()


def test_postgres_blocks_validated_direct_writes_and_allows_legal_flow(
    postgres_env: tuple[Engine, sessionmaker[Session]],
) -> None:
    _, factory = postgres_env
    project_id, performer_id, validator_id = uuid4(), uuid4(), uuid4()
    scope, _, sensor = _seed_project(
        factory,
        project_id=project_id,
        performer_id=performer_id,
        validator_id=validator_id,
    )
    profile = make_profile(project_id)
    add_profile(factory, scope, profile)
    validated = seed_validated(
        factory,
        scope,
        profile,
        target_id=sensor.sensor_id,
        performer=performer_id,
        validator=validator_id,
    )

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(checksum="sha256:evil")
            )
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(status=CalibrationStatus.DRAFT.value)
            )
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CalibrationRecordModel)
                .where(CalibrationRecordModel.id == validated.calibration_id)
                .values(
                    status=CalibrationStatus.ACTIVE.value,
                    checksum="sha256:evil",
                )
            )
        session.rollback()

    with factory() as session:
        row = session.scalar(
            select(CalibrationRecordModel).where(
                CalibrationRecordModel.id == validated.calibration_id
            )
        )
        assert row is not None
        assert row.status == CalibrationStatus.VALIDATED.value
        assert row.checksum == "sha256:abc"

    # 正常流程不受影响：VALIDATED → ACTIVE。
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope, validated.calibration_id)
        assert stored is not None
        activated = repository.activate(
            scope,
            stored,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()
    assert activated.status is CalibrationStatus.ACTIVE
    assert activated.validated_at == VALIDATED_AT


def test_postgres_rejects_missing_project_target_projection(
    postgres_env: tuple[Engine, sessionmaker[Session]],
) -> None:
    """即使绕过仓储直接插入设备，标定记录仍必须命中投影行。"""
    _, factory = postgres_env
    project_id, performer_id, validator_id = uuid4(), uuid4(), uuid4()
    scope, _, _ = _seed_project(
        factory,
        project_id=project_id,
        performer_id=performer_id,
        validator_id=validator_id,
    )
    profile = make_profile(project_id)
    add_profile(factory, scope, profile)

    unregistered_target = uuid4()
    with factory() as session:
        session.add(
            _direct_record(
                project_id=project_id,
                profile_id=profile.profile_id,
                target_id=unregistered_target,
                performer_id=performer_id,
                validator_id=validator_id,
                status=CalibrationStatus.DRAFT,
                checksum="sha256:" + "1" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # 正向对照：仓储 add 会维护投影行，正常创建/提交/校验/激活成功。
    system_profile = make_profile(
        project_id,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )
    add_profile(factory, scope, system_profile)
    draft = draft_record(
        project_id,
        system_profile.profile_id,
        target_id=scope.project_id,
        performer=performer_id,
        scope=CalibrationScope.SYSTEM,
        target_type=CalibrationTargetType.SYSTEM,
    )
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        repository.add(scope, draft)
        submitted = repository.submit(scope, draft, expected_version=1)
        session.commit()
    assert submitted.status is CalibrationStatus.SUBMITTED
