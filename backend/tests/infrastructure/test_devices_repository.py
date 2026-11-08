"""基础设施测试：设备域唯一约束、项目隔离与乐观锁（SQLite 真实数据库）。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.devices import (
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
from app.infrastructure.db.models.devices import Robot as RobotModel
from app.infrastructure.db.models.devices import Sensor as SensorModel
from app.infrastructure.repositories.devices import (
    SqlDeviceRepository,
    SqlRobotRepository,
    SqlSensorRepository,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.conftest import DeviceIds


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


def test_robot_roundtrip_and_reads_are_project_scoped(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    robot = _robot(ids.project_a)
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)

    with factory() as session:
        SqlRobotRepository(session).add(scope_a, robot)
        session.commit()

    with factory() as session:
        repository = SqlRobotRepository(session)
        stored = repository.get(scope_a, robot.robot_id)
        assert stored is not None
        assert stored.code == robot.code
        assert repository.get(scope_b, robot.robot_id) is None
        assert repository.get_by_code(scope_b, robot.code) is None
        assert robot.robot_id not in [item.robot_id for item in repository.list(scope_b)]
        assert [item.robot_id for item in repository.list(scope_a)].count(robot.robot_id) == 1


def test_robot_code_and_serial_number_are_unique_within_project(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)

    with factory() as session:
        SqlRobotRepository(session).add(
            scope_a,
            _robot(ids.project_a, code="r-1", serial_number="sn-1"),
        )
        session.commit()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_a, code="r-1", serial_number="sn-2"),
            )
        session.rollback()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_a, code="r-2", serial_number="sn-1"),
            )
        session.rollback()

    # 相同业务编码与序列号在不同项目可以共存。
    with factory() as session:
        SqlRobotRepository(session).add(
            scope_b,
            _robot(ids.project_b, code="r-1", serial_number="sn-1"),
        )
        session.commit()


def test_device_code_is_unique_within_project(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        SqlDeviceRepository(session).add(
            scope_a,
            _device(ids.project_a, code="d-1"),
        )
        session.commit()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlDeviceRepository(session).add(
                scope_a,
                _device(ids.project_a, code="d-1"),
            )
        session.rollback()

    with factory() as session:
        SqlDeviceRepository(session).add(
            ProjectScope(ids.project_b, actor_id=ids.user_b),
            _device(ids.project_b, code="d-1"),
        )
        session.commit()


def test_sensor_code_is_unique_and_reads_are_project_scoped(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    scope_b = ProjectScope(ids.project_b, actor_id=ids.user_b)
    device = _device(ids.project_a)
    robot_b = _robot(ids.project_b)

    with factory() as session:
        SqlDeviceRepository(session).add(scope_a, device)
        SqlRobotRepository(session).add(scope_b, robot_b)
        session.commit()

    sensor = _sensor(ids.project_a, code="s-1", device_id=device.device_id)
    with factory() as session:
        SqlSensorRepository(session).add(scope_a, sensor)
        session.commit()

    with factory() as session:
        repository = SqlSensorRepository(session)
        assert repository.get(scope_a, sensor.sensor_id) is not None
        assert repository.get(scope_b, sensor.sensor_id) is None
        assert repository.get_by_code(scope_b, sensor.code) is None
        assert sensor.sensor_id not in [item.sensor_id for item in repository.list(scope_b)]

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlSensorRepository(session).add(
                scope_a,
                _sensor(ids.project_a, code="s-1", device_id=device.device_id),
            )
        session.rollback()

    with factory() as session:
        SqlSensorRepository(session).add(
            scope_b,
            _sensor(ids.project_b, code="s-1", robot_id=robot_b.robot_id),
        )
        session.commit()


def test_repository_rejects_cross_project_writes(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)

    with factory() as session:
        with pytest.raises(CrossProjectAccessError):
            SqlRobotRepository(session).add(
                scope_a,
                _robot(ids.project_b),
            )


def test_sqlite_rejects_cross_project_device_robot_reference(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    """项目 A 的设备引用项目 B 的机器人必须由复合外键拒绝。"""
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    cross_project_device = Device(
        device_id=uuid4(),
        project_id=ids.project_a,
        code="cross-project-device",
        name="跨项目设备",
        device_type=DeviceType.COMPUTE,
        robot_id=ids.robot_b,
    )

    with factory() as session:
        with pytest.raises(IntegrityError):
            SqlDeviceRepository(session).add(scope_a, cross_project_device)
        session.rollback()

    with factory() as session:
        assert SqlDeviceRepository(session).get(scope_a, cross_project_device.device_id) is None


def test_sqlite_rejects_cross_project_sensor_robot_reference(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    """项目 A 的传感器引用项目 B 的机器人必须由复合外键拒绝。"""
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    cross_project_sensor = _sensor(
        ids.project_a,
        code="cross-project-sensor",
        robot_id=ids.robot_b,
    )

    with factory() as session:
        with pytest.raises(IntegrityError):
            SqlSensorRepository(session).add(scope_a, cross_project_sensor)
        session.rollback()

    with factory() as session:
        assert SqlSensorRepository(session).get(scope_a, cross_project_sensor.sensor_id) is None


def test_stale_version_is_rejected(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    robot = _robot(ids.project_a)

    with factory() as session:
        SqlRobotRepository(session).add(scope_a, robot)
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
        )
        session.commit()

    # 旧对象仍是 version=1，写入时被乐观锁拒绝。
    with factory() as session:
        with pytest.raises(VersionConflictError):
            SqlRobotRepository(session).save(
                scope_a,
                robot.activate(),
                expected_version=1,
            )
        session.rollback()


def test_sensor_mount_check_is_enforced_by_database(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    factory, ids = device_db
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


def test_repository_derives_audit_fields_from_scope(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    """created_by/updated_by 只能来自服务端 scope，调用方无法另行传入。"""
    factory, ids = device_db
    robot = _robot(ids.project_a)
    scope = ProjectScope(ids.project_a, actor_id=ids.user_b)

    with factory() as session:
        SqlRobotRepository(session).add(scope, robot)
        session.commit()

    with factory() as session:
        row = session.get(RobotModel, robot.robot_id)
        assert row is not None
        assert row.created_by == ids.user_b
        assert row.updated_by == ids.user_b
