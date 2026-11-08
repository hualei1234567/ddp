"""PostgreSQL 项目隔离集成测试入口。

CI 的后端任务已注入 ``DDP_DATABASE_URL``，因此本模块会在 CI 的 PostgreSQL
上真实执行一次跨项目复合外键负向测试；本地未配置时自动跳过并标注 BLOCKED。
也可用 ``DDP_TEST_POSTGRES_URL`` 显式指定独立测试库。

测试在随机 schema 中建表，避免污染 Alembic 管理的 public schema。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from app.domain.devices import Device, DeviceType, Robot
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.devices import SqlDeviceRepository, SqlRobotRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_it_{uuid4().hex}"
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
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def test_postgres_enforces_cross_project_composite_foreign_key(
    postgres_engine: Engine,
) -> None:
    factory = create_session_factory(postgres_engine)
    project_a, project_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    robot_b = Robot(
        robot_id=uuid4(),
        project_id=project_b,
        code="robot-b",
        name="项目 B 机器人",
        robot_type="HUMANOID",
    )
    scope_a = ProjectScope(project_a, actor_id=user_a)
    scope_b = ProjectScope(project_b, actor_id=user_b)

    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=user_a, display_name="甲"),
                User(id=user_b, display_name="乙"),
            ]
        )
        session.flush()
        SqlRobotRepository(session).add(scope_b, robot_b)
        session.commit()

    cross_project_device = Device(
        device_id=uuid4(),
        project_id=project_a,
        code="device-a",
        name="项目 A 设备",
        device_type=DeviceType.COMPUTE,
        robot_id=robot_b.robot_id,
    )
    with factory() as session:
        with pytest.raises(IntegrityError):
            SqlDeviceRepository(session).add(scope_a, cross_project_device)
        session.rollback()

    # 正向对照：同一项目内引用可以成功，且跨项目读取不可见。
    local_robot = Robot(
        robot_id=uuid4(),
        project_id=project_a,
        code="robot-a",
        name="项目 A 机器人",
        robot_type="HUMANOID",
    )
    local_device = Device(
        device_id=uuid4(),
        project_id=project_a,
        code="device-a-local",
        name="项目 A 本地设备",
        device_type=DeviceType.COMPUTE,
        robot_id=local_robot.robot_id,
    )
    with factory() as session:
        SqlRobotRepository(session).add(scope_a, local_robot)
        SqlDeviceRepository(session).add(scope_a, local_device)
        session.commit()

    with factory() as session:
        repository = SqlDeviceRepository(session)
        assert repository.get(scope_a, local_device.device_id) is not None
        assert repository.get(scope_b, local_device.device_id) is None
        assert repository.get(scope_a, cross_project_device.device_id) is None
