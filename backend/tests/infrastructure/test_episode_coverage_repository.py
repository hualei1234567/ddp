"""缺口覆盖统计仓储测试（FBK-002）。

覆盖三件事：

1. SQL 聚合结果正确（场景/设备 JSON 数组展开、机器人/质量状态分组）；
2. 只统计「可覆盖」状态（ACCEPTED/COMPLETED），半成品与终态不计入；
3. 强制 project 隔离：A 项目的统计绝不包含 B 项目的 Episode。

Episode 行按真实状态机推进（数据库兜底不允许直插非 PENDING，且 READY 起冻结
数据字段），所以业务字段在插入时就写好，之后再走合法迁移。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.episode_coverage import SqlEpisodeCoverageDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.coverage_support import NOW as coverage_now
from tests.infrastructure.coverage_support import SHA as coverage_sha
from tests.infrastructure.coverage_support import episode_row as _episode
from tests.infrastructure.coverage_support import insert_episodes as _insert

NOW = coverage_now
SHA = coverage_sha


@pytest.fixture()
def coverage_db() -> Iterator[tuple[sessionmaker[Session], UUID, UUID, UUID]]:
    """两个项目 + 一个操作者；返回 (factory, project_a, project_b, user_id)。"""
    engine: Engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b, user_id = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="coverage-a", name="覆盖项目 A"),
                Project(id=project_b, code="coverage-b", name="覆盖项目 B"),
                User(id=user_id, display_name="统计用户"),
            ]
        )
        session.commit()
    try:
        yield factory, project_a, project_b, user_id
    finally:
        engine.dispose()


def _coverage(factory, project_id: UUID, user_id: UUID, dimensions: frozenset[str] | None = None):
    with factory() as session:
        return SqlEpisodeCoverageDirectory(session).coverage_by_dimension(
            ProjectScope(project_id, actor_id=user_id),
            dimension_types=dimensions,
        )


def test_scene_coverage_expands_json_array_and_counts_distinct_episodes(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    robot = uuid4()
    _insert(
        factory,
        [
            # 一条 Episode 同时属于两个场景：两个场景各算 1，互不吞并。
            _episode(project_a, user_id, scene_labels=["kitchen", "warehouse"], robot_id=robot),
            _episode(project_a, user_id, scene_labels=["kitchen"], robot_id=robot),
            # 空数组不产生任何场景行（不会凭空出现空字符串维度值）。
            _episode(project_a, user_id, scene_labels=[]),
        ],
    )
    entries = _coverage(factory, project_a, user_id, frozenset({"SCENE"}))
    counts = {entry.dimension_value: entry.count for entry in entries}
    assert counts == {"kitchen": 2, "warehouse": 1}
    assert all(entry.dimension_type == "SCENE" for entry in entries)


def test_device_coverage_uses_distinct_episode_count(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    device_a, device_b = str(uuid4()), str(uuid4())
    _insert(
        factory,
        [
            _episode(project_a, user_id, device_ids=[device_a, device_b]),
            _episode(project_a, user_id, device_ids=[device_a]),
        ],
    )
    entries = _coverage(factory, project_a, user_id, frozenset({"DEVICE"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {
        device_a: 2,
        device_b: 1,
    }


def test_scalar_dimensions_group_by_robot_and_quality_status(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    robot_a, robot_b = uuid4(), uuid4()
    _insert(
        factory,
        [
            _episode(project_a, user_id, robot_id=robot_a, quality_status="PASS"),
            _episode(project_a, user_id, robot_id=robot_a, quality_status="PASS"),
            _episode(project_a, user_id, robot_id=robot_b, quality_status="WARN"),
            # robot_id 为空的 Episode 不产生 ROBOT 维度行（不臆造空值分组）。
            _episode(project_a, user_id, robot_id=None, quality_status="PASS"),
        ],
    )
    entries = _coverage(factory, project_a, user_id, frozenset({"ROBOT", "QUALITY_STATUS"}))
    by_dimension: dict[str, dict[str, int]] = {"ROBOT": {}, "QUALITY_STATUS": {}}
    for entry in entries:
        by_dimension[entry.dimension_type][entry.dimension_value] = entry.count
    assert by_dimension["ROBOT"] == {str(robot_a): 2, str(robot_b): 1}
    assert by_dimension["QUALITY_STATUS"] == {"PASS": 3, "WARN": 1}


def test_only_coverable_statuses_are_counted(coverage_db) -> None:
    """READY 起（数据已冻结）才计入；PENDING 与终态 INVALIDATED 不计。"""
    factory, project_a, _project_b, user_id = coverage_db
    _insert(
        factory,
        [
            _episode(project_a, user_id, target_status="ACCEPTED", scene_labels=["kitchen"]),
            _episode(project_a, user_id, target_status="COMPLETED", scene_labels=["kitchen"]),
            _episode(project_a, user_id, target_status="READY", scene_labels=["kitchen"]),
            _episode(project_a, user_id, target_status="PENDING", scene_labels=["kitchen"]),
            _episode(project_a, user_id, target_status="INVALIDATED", scene_labels=["kitchen"]),
        ],
    )
    entries = _coverage(factory, project_a, user_id, frozenset({"SCENE"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {"kitchen": 3}


def test_project_isolation_excludes_other_project_episodes(coverage_db) -> None:
    factory, project_a, project_b, user_id = coverage_db
    robot_a, robot_b = uuid4(), uuid4()
    _insert(
        factory,
        [
            _episode(project_a, user_id, scene_labels=["kitchen"], robot_id=robot_a),
            _episode(project_b, user_id, scene_labels=["kitchen"], robot_id=robot_b),
        ],
    )
    entries_a = _coverage(factory, project_a, user_id, frozenset({"SCENE", "ROBOT"}))
    entries_b = _coverage(factory, project_b, user_id, frozenset({"SCENE", "ROBOT"}))
    assert {e.dimension_value: e.count for e in entries_a if e.dimension_type == "SCENE"} == {
        "kitchen": 1
    }
    assert {e.dimension_value: e.count for e in entries_b if e.dimension_type == "SCENE"} == {
        "kitchen": 1
    }
    assert {e.dimension_value for e in entries_a if e.dimension_type == "ROBOT"} == {str(robot_a)}
    assert {e.dimension_value for e in entries_b if e.dimension_type == "ROBOT"} == {str(robot_b)}


def test_measured_at_is_latest_updated_at(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    old = NOW - timedelta(days=10)
    _insert(
        factory,
        [
            _episode(project_a, user_id, scene_labels=["kitchen"], updated_at=old),
            _episode(project_a, user_id, scene_labels=["kitchen"], updated_at=NOW),
        ],
    )
    entries = _coverage(factory, project_a, user_id, frozenset({"SCENE"}))
    # SQLite 不保留时区，仓储按 UTC 归一。
    assert entries[0].measured_at == NOW


def test_empty_dimension_selection_returns_no_rows(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    _insert(factory, [_episode(project_a, user_id, scene_labels=["kitchen"])])
    assert _coverage(factory, project_a, user_id, frozenset()) == []


def test_unknown_dimension_is_rejected_not_silently_ignored(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    with factory() as session:
        repo = SqlEpisodeCoverageDirectory(session)
        with pytest.raises(ValueError, match="不支持的覆盖维度"):
            repo.coverage_by_dimension(
                ProjectScope(project_a, actor_id=user_id),
                dimension_types=frozenset({"NOT_A_DIMENSION"}),
            )


@pytest.mark.parametrize("dimension", ["ACTION", "LABEL", "MODALITY"])
def test_closed_dimension_is_supported_but_unmeasurable_when_population_incomplete(
    coverage_db, dimension: str
) -> None:
    """三个封闭轴已接入实测；但这条 Episode 三轴都缺权威来源，必须返回空而不是 0。"""
    factory, project_a, _project_b, user_id = coverage_db
    _insert(factory, [_episode(project_a, user_id, scene_labels=["kitchen"])])
    entries = _coverage(factory, project_a, user_id, frozenset({dimension}))
    assert entries == []


def test_default_dimension_set_covers_all_measurable_axes(coverage_db) -> None:
    factory, project_a, _project_b, user_id = coverage_db
    _insert(
        factory,
        [
            _episode(
                project_a,
                user_id,
                scene_labels=["kitchen"],
                robot_id=uuid4(),
                device_ids=[str(uuid4())],
            )
        ],
    )
    entries = _coverage(factory, project_a, user_id)
    assert {entry.dimension_type for entry in entries} == {
        "SCENE",
        "ROBOT",
        "DEVICE",
        "QUALITY_STATUS",
    }
