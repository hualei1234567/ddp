"""基础设施测试：设备域唯一约束、项目隔离与乐观锁（SQLite 真实数据库）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from app.domain.devices import (
    CalibrationProfile,
    CalibrationRecord,
    CalibrationScope,
    CalibrationStatus,
    CalibrationTargetType,
    CalibrationType,
    Device,
    DeviceType,
    DuplicateResourceError,
    LifecycleStatus,
    Robot,
    Sensor,
    SensorType,
    VersionConflictError,
)
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.devices import Sensor as SensorModel
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.devices import (
    SqlCalibrationProfileRepository,
    SqlCalibrationRecordRepository,
    SqlDeviceRepository,
    SqlRobotRepository,
    SqlSensorRepository,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


class _Ids(NamedTuple):
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID


def _factory() -> tuple[Engine, sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


@pytest.fixture()
def db() -> Iterator[tuple[sessionmaker[Session], _Ids]]:
    engine, factory = _factory()
    ids = _Ids(
        project_a=uuid4(),
        project_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
    )
    with factory() as session:
        session.add_all(
            [
                Project(id=ids.project_a, code="project-a", name="项目 A"),
                Project(id=ids.project_b, code="project-b", name="项目 B"),
                User(id=ids.user_a, display_name="甲"),
                User(id=ids.user_b, display_name="乙"),
            ]
        )
        session.commit()
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _robot(
    project_id: UUID,
    *,
    code: str = "robot-1",
    serial_number: str = "sn-robot-1",
) -> Robot:
    return Robot(
        robot_id=uuid4(),
        project_id=project_id,
        code=code,
        name="机器人一",
        robot_type="HUMANOID",
        serial_number=serial_number,
    )


def _device(project_id: UUID, *, code: str = "device-1") -> Device:
    return Device(
        device_id=uuid4(),
        project_id=project_id,
        code=code,
        name="设备一",
        device_type=DeviceType.COMPUTE,
    )


def _sensor(
    project_id: UUID,
    *,
    code: str = "sensor-1",
    robot_id: UUID | None = None,
    device_id: UUID | None = None,
) -> Sensor:
    return Sensor(
        sensor_id=uuid4(),
        project_id=project_id,
        code=code,
        sensor_type=SensorType.RGB,
        sample_rate_hz=30.0,
        coordinate_frame="camera_link",
        robot_id=robot_id,
        device_id=device_id,
    )


def _profile(project_id: UUID, *, version: int = 1) -> CalibrationProfile:
    return CalibrationProfile(
        profile_id=uuid4(),
        project_id=project_id,
        name="默认标定配置",
        scope=CalibrationScope.SENSOR,
        target_type=CalibrationTargetType.SENSOR,
        version=version,
        schema_version="ddp.calibration/1.0.0",
        required_types=(CalibrationType.INTRINSICS,),
    )


def _calibration_record(
    project_id: UUID,
    profile_id: UUID,
    *,
    target_id: UUID,
    performer: UUID,
    validator: UUID,
    status: CalibrationStatus = CalibrationStatus.VALIDATED,
) -> CalibrationRecord:
    return CalibrationRecord(
        calibration_id=uuid4(),
        project_id=project_id,
        profile_id=profile_id,
        scope=CalibrationScope.SENSOR,
        target_type=CalibrationTargetType.SENSOR,
        target_id=target_id,
        calibration_type=CalibrationType.INTRINSICS,
        method="charuco",
        parameters={"fx": 600.0, "fy": 600.0},
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        evidence_asset_ids=(uuid4(),),
        checksum="sha256:abc",
        performed_by=performer,
        validated_by=validator,
        validated_at=datetime(2026, 1, 2, tzinfo=UTC),
        status=status,
    )


def test_robot_roundtrip_and_reads_are_project_scoped(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    robot = _robot(ids.project_a)
    scope_a = ProjectScope(ids.project_a)
    scope_b = ProjectScope(ids.project_b)

    with factory() as session:
        SqlRobotRepository(session).add(scope_a, robot, actor_id=ids.user_a)
        session.commit()

    with factory() as session:
        repository = SqlRobotRepository(session)
        stored = repository.get(scope_a, robot.robot_id)
        assert stored is not None
        assert stored.code == robot.code
        assert repository.get(scope_b, robot.robot_id) is None
        assert repository.get_by_code(scope_b, robot.code) is None
        assert repository.list(scope_b) == []
        assert [item.robot_id for item in repository.list(scope_a)] == [robot.robot_id]


def test_robot_code_and_serial_number_are_unique_within_project(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)
    scope_b = ProjectScope(ids.project_b)

    with factory() as session:
        SqlRobotRepository(session).add(
            scope_a,
            _robot(ids.project_a, code="r-1", serial_number="sn-1"),
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_a, code="r-1", serial_number="sn-2"),
                actor_id=ids.user_a,
            )
        session.rollback()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_a, code="r-2", serial_number="sn-1"),
                actor_id=ids.user_a,
            )
        session.rollback()

    # 相同业务编码与序列号在不同项目可以共存。
    with factory() as session:
        SqlRobotRepository(session).add(
            scope_b,
            _robot(ids.project_b, code="r-1", serial_number="sn-1"),
            actor_id=ids.user_b,
        )
        session.commit()


def test_device_code_is_unique_within_project(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)

    with factory() as session:
        SqlDeviceRepository(session).add(
            scope_a,
            _device(ids.project_a, code="d-1"),
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlDeviceRepository(session).add(
                scope_a,
                _device(ids.project_a, code="d-1"),
                actor_id=ids.user_a,
            )
        session.rollback()

    with factory() as session:
        SqlDeviceRepository(session).add(
            ProjectScope(ids.project_b),
            _device(ids.project_b, code="d-1"),
            actor_id=ids.user_b,
        )
        session.commit()


def test_sensor_code_is_unique_and_reads_are_project_scoped(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)
    scope_b = ProjectScope(ids.project_b)
    device = _device(ids.project_a)
    robot_b = _robot(ids.project_b)

    with factory() as session:
        SqlDeviceRepository(session).add(scope_a, device, actor_id=ids.user_a)
        SqlRobotRepository(session).add(scope_b, robot_b, actor_id=ids.user_b)
        session.commit()

    sensor = _sensor(ids.project_a, code="s-1", device_id=device.device_id)
    with factory() as session:
        SqlSensorRepository(session).add(scope_a, sensor, actor_id=ids.user_a)
        session.commit()

    with factory() as session:
        repository = SqlSensorRepository(session)
        assert repository.get(scope_a, sensor.sensor_id) is not None
        assert repository.get(scope_b, sensor.sensor_id) is None
        assert repository.get_by_code(scope_b, sensor.code) is None
        assert repository.list(scope_b) == []

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlSensorRepository(session).add(
                scope_a,
                _sensor(ids.project_a, code="s-1", device_id=device.device_id),
                actor_id=ids.user_a,
            )
        session.rollback()

    with factory() as session:
        SqlSensorRepository(session).add(
            scope_b,
            _sensor(ids.project_b, code="s-1", robot_id=robot_b.robot_id),
            actor_id=ids.user_b,
        )
        session.commit()


def test_repository_rejects_cross_project_writes(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)

    with factory() as session:
        with pytest.raises(CrossProjectAccessError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_b),
                actor_id=ids.user_a,
            )


def test_stale_version_is_rejected(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)
    robot = _robot(ids.project_a)

    with factory() as session:
        SqlRobotRepository(session).add(scope_a, robot, actor_id=ids.user_a)
        session.commit()

    # 另一个会话把版本推进到 2。
    with factory() as session:
        repository = SqlRobotRepository(session)
        current = repository.get(scope_a, robot.robot_id)
        assert current is not None
        repository.save(
            scope_a,
            current.activate(),
            expected_version=current.version,
            actor_id=ids.user_a,
        )
        session.commit()

    # 旧对象仍是 version=1，写入时被乐观锁拒绝。
    with factory() as session:
        with pytest.raises(VersionConflictError):
            SqlRobotRepository(session).save(
                scope_a,
                robot.activate(),
                expected_version=1,
                actor_id=ids.user_a,
            )
        session.rollback()


def test_sensor_mount_check_is_enforced_by_database(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    with factory() as session:
        session.add(
            SensorModel(
                id=uuid4(),
                project_id=ids.project_a,
                code="sensor-without-mount",
                sensor_type=SensorType.RGB.value,
                status=LifecycleStatus.PROVISIONED.value,
                sample_rate_hz=30.0,
                coordinate_frame="camera_link",
                sync_mode="NONE",
                created_by=ids.user_a,
                updated_by=ids.user_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_calibration_profile_accepts_non_initial_config_version(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)
    scope_b = ProjectScope(ids.project_b)
    profile = _profile(ids.project_a, version=2)

    with factory() as session:
        SqlCalibrationProfileRepository(session).add(
            scope_a,
            profile,
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        repository = SqlCalibrationProfileRepository(session)
        stored = repository.get(scope_a, profile.profile_id)
        assert stored is not None
        assert stored.version == 2
        assert repository.get(scope_b, profile.profile_id) is None


def test_activation_supersedes_previous_active_and_is_project_scoped(
    db: tuple[sessionmaker[Session], _Ids],
) -> None:
    factory, ids = db
    scope_a = ProjectScope(ids.project_a)
    scope_b = ProjectScope(ids.project_b)
    profile = _profile(ids.project_a)
    target_id = uuid4()

    with factory() as session:
        SqlCalibrationProfileRepository(session).add(
            scope_a,
            profile,
            actor_id=ids.user_a,
        )
        session.commit()

    first = _calibration_record(
        ids.project_a,
        profile.profile_id,
        target_id=target_id,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(
            scope_a,
            first,
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, first.calibration_id)
        assert stored is not None
        activated = repository.activate(
            scope_a,
            stored,
            expected_version=stored.version,
            actor_id=ids.user_a,
        )
        assert activated.status is CalibrationStatus.ACTIVE
        session.commit()

    second = _calibration_record(
        ids.project_a,
        profile.profile_id,
        target_id=target_id,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    with factory() as session:
        SqlCalibrationRecordRepository(session).add(
            scope_a,
            second,
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        repository.activate(
            scope_a,
            stored,
            expected_version=stored.version,
            actor_id=ids.user_a,
        )
        session.commit()

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        assert old is not None
        assert old.status is CalibrationStatus.SUPERSEDED
        # 激活时版本 1→2，被后续记录替代时再 2→3。
        assert old.version == 3
        current = repository.get(scope_a, second.calibration_id)
        assert current is not None
        assert current.status is CalibrationStatus.ACTIVE
        assert repository.get(scope_b, first.calibration_id) is None
        assert repository.list(scope_b) == []

    # 同一项目/作用域/目标/类型只允许一条 ACTIVE 记录。
    duplicate_active = _calibration_record(
        ids.project_a,
        profile.profile_id,
        target_id=target_id,
        performer=ids.user_a,
        validator=ids.user_b,
        status=CalibrationStatus.ACTIVE,
    )
    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlCalibrationRecordRepository(session).add(
                scope_a,
                duplicate_active,
                actor_id=ids.user_a,
            )
        session.rollback()
