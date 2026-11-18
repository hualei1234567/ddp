"""场地资源计数适配器测试（N9-005）。

用真实 SQLite + 真实 ORM 行，不做 mock。重点是三类真实故障：

1. **项目隔离**：另一项目的设备/任务不得混入本项目按场地聚合的结果；
2. **计数语义**：传感器经设备归属场地；未指定场地的行不计入任何基地；
3. **不做笛卡尔积**：设备与任务数分别聚合后合并，不能因 JOIN 翻倍。

本适配器只读，因此没有写路径测试；空 ``location_ids`` 必须直接返回空映射，
不能退化成"查全项目再过滤"。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.db.models.devices import Device, Robot, Sensor
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.models.locations import LocationModel
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.location_resources import (
    SqlSiteResourceSummaryRepository,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class Ids:
    project_a: UUID
    project_b: UUID
    user_id: UUID
    location_a1: UUID
    location_a2: UUID
    location_b1: UUID


@pytest.fixture()
def resource_db() -> Iterator[tuple[sessionmaker[Session], Ids]]:
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = Ids(
        project_a=uuid4(),
        project_b=uuid4(),
        user_id=uuid4(),
        location_a1=uuid4(),
        location_a2=uuid4(),
        location_b1=uuid4(),
    )
    now = datetime.now(UTC)
    with factory() as session:
        session.add_all(
            [
                Project(id=ids.project_a, code="project-a", name="项目 A"),
                Project(id=ids.project_b, code="project-b", name="项目 B"),
                User(id=ids.user_id, display_name="用户一"),
            ]
        )
        session.flush()
        session.add_all(
            [
                _location_row(ids.project_a, ids.location_a1, "BJ-1", now, ids.user_id),
                _location_row(ids.project_a, ids.location_a2, "BJ-2", now, ids.user_id),
                _location_row(ids.project_b, ids.location_b1, "SH-1", now, ids.user_id),
            ]
        )
        session.commit()
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _location_row(
    project_id: UUID,
    location_id: UUID,
    code: str,
    now: datetime,
    user_id: UUID,
) -> LocationModel:
    return LocationModel(
        id=location_id,
        project_id=project_id,
        code=code,
        name=f"基地 {code}",
        kind="SITE",
        version=1,
        created_at=now,
        updated_at=now,
        created_by=user_id,
        updated_by=user_id,
    )


def _device(
    ids: Ids,
    *,
    project_id: UUID,
    code: str,
    location_id: UUID | None,
) -> Device:
    return Device(
        id=uuid4(),
        project_id=project_id,
        code=code,
        name=f"设备 {code}",
        device_type="COMPUTE",
        status="ACTIVE",
        location_id=location_id,
        created_by=ids.user_id,
        updated_by=ids.user_id,
    )


def _sensor(
    ids: Ids,
    *,
    project_id: UUID,
    code: str,
    device_id: UUID | None = None,
    robot_id: UUID | None = None,
) -> Sensor:
    return Sensor(
        id=uuid4(),
        project_id=project_id,
        code=code,
        sensor_type="RGB",
        robot_id=robot_id,
        device_id=device_id,
        status="ACTIVE",
        sample_rate_hz=30.0,
        coordinate_frame="camera_link",
        sync_mode="NONE",
        created_by=ids.user_id,
        updated_by=ids.user_id,
    )


def _task(ids: Ids, *, project_id: UUID, code: str, location_id: UUID | None) -> CollectionTask:
    return CollectionTask(
        id=uuid4(),
        project_id=project_id,
        code=code,
        name=f"采集任务 {code}",
        status="DRAFT",
        priority="P1",
        location_id=location_id,
        created_by=ids.user_id,
        updated_by=ids.user_id,
    )


def _robot(ids: Ids, *, project_id: UUID, code: str) -> Robot:
    return Robot(
        id=uuid4(),
        project_id=project_id,
        code=code,
        name=f"机器人 {code}",
        robot_type="HUMANOID",
        status="ACTIVE",
        created_by=ids.user_id,
        updated_by=ids.user_id,
    )


def test_counts_are_grouped_per_location_and_isolated_by_project(
    resource_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = resource_db
    with factory() as session:
        device_a1 = _device(
            ids, project_id=ids.project_a, code="a-dev-1", location_id=ids.location_a1
        )
        device_no_location = _device(
            ids, project_id=ids.project_a, code="a-dev-none", location_id=None
        )
        device_b1 = _device(
            ids, project_id=ids.project_b, code="b-dev-1", location_id=ids.location_b1
        )
        robot_a = _robot(ids, project_id=ids.project_a, code="a-robot")
        session.add_all([device_a1, device_no_location, device_b1, robot_a])
        session.flush()
        session.add_all(
            [
                _sensor(ids, project_id=ids.project_a, code="a-cam-1", device_id=device_a1.id),
                _sensor(ids, project_id=ids.project_a, code="a-cam-2", device_id=device_a1.id),
                # 仅挂机器人：无法归因到场地，不得计入任何基地。
                _sensor(ids, project_id=ids.project_a, code="a-robot-cam", robot_id=robot_a.id),
                _task(ids, project_id=ids.project_a, code="a-task-1", location_id=ids.location_a1),
                _task(ids, project_id=ids.project_b, code="b-task-1", location_id=ids.location_b1),
            ]
        )
        session.commit()

        summary = SqlSiteResourceSummaryRepository(session)
        counts = summary.count_by_location(
            ProjectScope(ids.project_a, actor_id=ids.user_id),
            location_ids=[ids.location_a1, ids.location_a2],
        )

        # 空基地（a2）不产生行，由用例回落到全 0；不能凭空造一条"有 0 个资源"
        # 的映射项——那会把"没查到"和"确实为零"混成一个约定。
        assert set(counts) == {ids.location_a1}
        a1 = counts[ids.location_a1]
        assert a1.device_count == 1
        assert a1.sensor_count == 2
        assert a1.collection_task_count == 1

        b_counts = summary.count_by_location(
            ProjectScope(ids.project_b, actor_id=ids.user_id),
            location_ids=[ids.location_b1],
        )
        assert set(b_counts) == {ids.location_b1}
        assert b_counts[ids.location_b1].device_count == 1
        assert b_counts[ids.location_b1].sensor_count == 0
        assert b_counts[ids.location_b1].collection_task_count == 1


def test_empty_location_ids_short_circuits(
    resource_db: tuple[sessionmaker[Session], Ids],
) -> None:
    factory, ids = resource_db
    with factory() as session:
        summary = SqlSiteResourceSummaryRepository(session)
        assert (
            summary.count_by_location(
                ProjectScope(ids.project_a, actor_id=ids.user_id),
                location_ids=[],
            )
            == {}
        )


def test_resources_without_location_are_not_attributed(
    resource_db: tuple[sessionmaker[Session], Ids],
) -> None:
    """``location_id`` 为空的历史行不得被折算到任何基地。"""
    factory, ids = resource_db
    with factory() as session:
        session.add_all(
            [
                _task(ids, project_id=ids.project_a, code="a-task-none", location_id=None),
                _device(ids, project_id=ids.project_a, code="a-dev-none", location_id=None),
            ]
        )
        session.commit()

        counts = SqlSiteResourceSummaryRepository(session).count_by_location(
            ProjectScope(ids.project_a, actor_id=ids.user_id),
            location_ids=[ids.location_a1, ids.location_a2],
        )
        assert counts == {}


def test_conflicting_location_ids_do_not_double_count(
    resource_db: tuple[sessionmaker[Session], Ids],
) -> None:
    """重复传入同一场地只应计数一次（IN 去重），防止调用方让计数翻倍。"""
    factory, ids = resource_db
    with factory() as session:
        session.add(
            _device(ids, project_id=ids.project_a, code="a-dev-1", location_id=ids.location_a1)
        )
        session.commit()

        counts = SqlSiteResourceSummaryRepository(session).count_by_location(
            ProjectScope(ids.project_a, actor_id=ids.user_id),
            location_ids=[ids.location_a1, ids.location_a1],
        )
        assert counts[ids.location_a1].device_count == 1
