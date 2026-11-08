"""设备域仓储测试共享夹具。

夹具真实写入 SQLite 数据库，预置两个项目、成员及标定目标（机器人/传感器），
避免各测试重复搭建项目上下文。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from app.domain.devices import Robot, Sensor, SensorType
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.devices import SqlRobotRepository, SqlSensorRepository
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class DeviceIds:
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID
    user_viewer: UUID
    robot_a: UUID
    robot_b: UUID
    sensor_a: UUID
    sensor_b: UUID


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
def device_db() -> Iterator[tuple[sessionmaker[Session], DeviceIds]]:
    engine, factory = _factory()
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b, user_viewer = uuid4(), uuid4(), uuid4()
    robot_a = Robot(
        robot_id=uuid4(),
        project_id=project_a,
        code="fixture-robot-a",
        name="夹具机器人 A",
        robot_type="HUMANOID",
    )
    robot_b = Robot(
        robot_id=uuid4(),
        project_id=project_b,
        code="fixture-robot-b",
        name="夹具机器人 B",
        robot_type="HUMANOID",
    )
    sensor_a = Sensor(
        sensor_id=uuid4(),
        project_id=project_a,
        code="fixture-sensor-a",
        sensor_type=SensorType.RGB,
        sample_rate_hz=30.0,
        coordinate_frame="camera_link",
        robot_id=robot_a.robot_id,
    )
    sensor_b = Sensor(
        sensor_id=uuid4(),
        project_id=project_b,
        code="fixture-sensor-b",
        sensor_type=SensorType.RGB,
        sample_rate_hz=30.0,
        coordinate_frame="camera_link",
        robot_id=robot_b.robot_id,
    )
    ids = DeviceIds(
        project_a=project_a,
        project_b=project_b,
        user_a=user_a,
        user_b=user_b,
        user_viewer=user_viewer,
        robot_a=robot_a.robot_id,
        robot_b=robot_b.robot_id,
        sensor_a=sensor_a.sensor_id,
        sensor_b=sensor_b.sensor_id,
    )

    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=user_a, display_name="甲"),
                User(id=user_b, display_name="乙"),
                User(id=user_viewer, display_name="只读用户"),
            ]
        )
        session.flush()
        session.add_all(
            [
                # A 项目：甲执行、乙校验、只读用户仅可读。
                ProjectMember(
                    project_id=project_a,
                    user_id=user_a,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=user_b,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=user_viewer,
                    role=ProjectRole.VIEWER.value,
                ),
                # B 项目：乙为 owner，甲为 editor（用于跨项目校验人场景）。
                ProjectMember(
                    project_id=project_b,
                    user_id=user_b,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_b,
                    user_id=user_a,
                    role=ProjectRole.EDITOR.value,
                ),
            ]
        )
        session.commit()

    with factory() as session:
        SqlRobotRepository(session).add(
            ProjectScope(project_a, actor_id=user_a),
            robot_a,
        )
        SqlRobotRepository(session).add(
            ProjectScope(project_b, actor_id=user_b),
            robot_b,
        )
        SqlSensorRepository(session).add(
            ProjectScope(project_a, actor_id=user_a),
            sensor_a,
        )
        SqlSensorRepository(session).add(
            ProjectScope(project_b, actor_id=user_b),
            sensor_b,
        )
        session.commit()

    try:
        yield factory, ids
    finally:
        engine.dispose()
