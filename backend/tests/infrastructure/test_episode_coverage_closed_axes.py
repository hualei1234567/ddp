"""缺口覆盖统计的三个封闭轴（ACTION / LABEL / MODALITY）。

守的是「封闭轴」的核心口径：

- 数据总体完整 → 每个维度值都有实测计数，缺席值补 ``count=0`` 的 MEASURED；
- 总体不完整（有 Episode 缺权威来源）→ 整轴不返回任何行（上游按 UNKNOWN/MANUAL），
  **绝不**用"只覆盖一部分 Episode 的数字"冒充权威结论；
- 统计按被引用的版本行（ACTION）、已批准记录（LABEL）、传感器类型（MODALITY）计。

每个测试都断言 ``complete`` 的两种结果，避免把"返回空"误读成"没问题"。
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.episode_coverage import SqlEpisodeCoverageDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.annotation_chain_support import approve_episodes
from tests.infrastructure.coverage_support import (
    NOW,
    add_action_spec,
    add_collection_task,
    add_robot,
    add_sensor,
    episode_row,
    insert_episodes,
)


@pytest.fixture()
def closed_db() -> Iterator[tuple[sessionmaker[Session], UUID, UUID, UUID]]:
    """两个项目 + 各一个成员；返回 (factory, project_a, project_b, user_id)。"""
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
                Project(id=project_a, code="closed-a", name="封闭轴 A"),
                Project(id=project_b, code="closed-b", name="封闭轴 B"),
                User(id=user_id, display_name="统计用户"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project_a, user_id=user_id, role="owner"),
                ProjectMember(project_id=project_b, user_id=user_id, role="owner"),
            ]
        )
        session.commit()
    try:
        yield factory, project_a, project_b, user_id
    finally:
        engine.dispose()


def _coverage(
    factory: sessionmaker[Session],
    project_id: UUID,
    user_id: UUID,
    dimension: str,
    *,
    wanted: set[tuple[str, str]] | None = None,
):
    with factory() as session:
        return SqlEpisodeCoverageDirectory(session).coverage_by_dimension(
            ProjectScope(project_id, actor_id=user_id),
            dimension_types=frozenset({dimension}),
            wanted_keys=wanted,
        )


def _counts(entries) -> dict[str, int]:
    return {item.dimension_value: item.count for item in entries}


# --------------------------------------------------------------------------- #
# MODALITY
# --------------------------------------------------------------------------- #
def test_modality_counts_episodes_per_sensor_type(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        robot = add_robot(session, project_a, user_id)
        rgb = add_sensor(session, project_a, user_id, robot_id=robot, sensor_type="RGB")
        depth = add_sensor(session, project_a, user_id, robot_id=robot, sensor_type="DEPTH")
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_a, user_id, sensor_ids=[str(rgb), str(depth)]),
            episode_row(project_a, user_id, sensor_ids=[str(rgb)]),
        ],
    )
    entries = _coverage(factory, project_a, user_id, "MODALITY")
    assert _counts(entries) == {"RGB": 2, "DEPTH": 1}
    assert all(entry.measured_at == NOW for entry in entries)


def test_modality_zero_fills_missing_value_only_when_population_complete(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        robot = add_robot(session, project_a, user_id)
        rgb = add_sensor(session, project_a, user_id, robot_id=robot, sensor_type="RGB")
        session.commit()
    insert_episodes(factory, [episode_row(project_a, user_id, sensor_ids=[str(rgb)])])
    entries = _coverage(
        factory,
        project_a,
        user_id,
        "MODALITY",
        wanted={("MODALITY", "AUDIO")},
    )
    # 查过全部 Episode 且都没有 AUDIO → 真实 0，不是 UNKNOWN。
    audio = next(item for item in entries if item.dimension_value == "AUDIO")
    assert audio.count == 0
    assert audio.measured_at == NOW


def test_modality_unknown_when_any_episode_declares_no_sensor(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        robot = add_robot(session, project_a, user_id)
        rgb = add_sensor(session, project_a, user_id, robot_id=robot, sensor_type="RGB")
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_a, user_id, sensor_ids=[str(rgb)]),
            # 这条没声明传感器：它的模态是"未知"，不能当成"没有 AUDIO"。
            episode_row(project_a, user_id),
        ],
    )
    entries = _coverage(
        factory,
        project_a,
        user_id,
        "MODALITY",
        wanted={("MODALITY", "AUDIO")},
    )
    assert entries == []


def test_modality_unknown_when_sensor_reference_dangles(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    insert_episodes(
        factory,
        [episode_row(project_a, user_id, sensor_ids=[str(uuid4())])],
    )
    # 带 wanted_keys：若实现误判为"总体完整"，会给 RGB 补一条实测 0；正确行为是
    # 解析不到传感器 → 整轴不可测 → 一条都不返回。
    assert (
        _coverage(
            factory,
            project_a,
            user_id,
            "MODALITY",
            wanted={("MODALITY", "RGB")},
        )
        == []
    )


def test_modality_isolated_per_project(closed_db) -> None:
    factory, project_a, project_b, user_id = closed_db
    with factory() as session:
        robot_a = add_robot(session, project_a, user_id)
        robot_b = add_robot(session, project_b, user_id)
        rgb_a = add_sensor(session, project_a, user_id, robot_id=robot_a, sensor_type="RGB")
        audio_b = add_sensor(session, project_b, user_id, robot_id=robot_b, sensor_type="AUDIO")
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_a, user_id, sensor_ids=[str(rgb_a)]),
            episode_row(project_b, user_id, sensor_ids=[str(audio_b)]),
        ],
    )
    assert _counts(_coverage(factory, project_a, user_id, "MODALITY")) == {"RGB": 1}
    assert _counts(_coverage(factory, project_b, user_id, "MODALITY")) == {"AUDIO": 1}


# --------------------------------------------------------------------------- #
# ACTION
# --------------------------------------------------------------------------- #
def test_action_counts_referenced_version_row(closed_db) -> None:
    """任务引用的是版本行：同 code 的新版本不得混入统计。"""
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        robot = add_robot(session, project_a, user_id)
        spec = add_action_spec(
            session,
            project_a,
            user_id,
            code="pick-place",
            actions=[
                {"code": "pick", "name": "抓取"},
                {"code": "place", "name": "放置"},
            ],
        )
        task = add_collection_task(session, project_a, user_id, action_spec_id=spec)
        # 同 code 的新版本行：内容不同，任务并未引用它。
        add_action_spec(
            session,
            project_a,
            user_id,
            code="pick-place",
            spec_version="v2",
            actions=[{"code": "push", "name": "推"}],
        )
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_a, user_id, task_id=task, robot_id=robot),
            episode_row(project_a, user_id, task_id=task, robot_id=robot),
        ],
    )
    entries = _coverage(factory, project_a, user_id, "ACTION")
    assert _counts(entries) == {"pick": 2, "place": 2}
    assert "push" not in _counts(entries)


def test_action_falls_back_to_name_when_code_missing(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        spec = add_action_spec(
            session,
            project_a,
            user_id,
            actions=[{"name": "放置"}],
        )
        task = add_collection_task(session, project_a, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(factory, [episode_row(project_a, user_id, task_id=task)])
    assert _counts(_coverage(factory, project_a, user_id, "ACTION")) == {"放置": 1}


def test_action_zero_fills_missing_value_when_population_complete(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        spec = add_action_spec(session, project_a, user_id, actions=[{"code": "pick"}])
        task = add_collection_task(session, project_a, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(factory, [episode_row(project_a, user_id, task_id=task)])
    entries = _coverage(
        factory,
        project_a,
        user_id,
        "ACTION",
        wanted={("ACTION", "push")},
    )
    push = next(item for item in entries if item.dimension_value == "push")
    assert push.count == 0
    assert push.measured_at == NOW


def test_action_unknown_when_episode_has_no_task(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        spec = add_action_spec(session, project_a, user_id, actions=[{"code": "pick"}])
        task = add_collection_task(session, project_a, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_a, user_id, task_id=task),
            episode_row(project_a, user_id),
        ],
    )
    assert _coverage(factory, project_a, user_id, "ACTION", wanted={("ACTION", "push")}) == []


def test_action_unknown_when_task_has_no_action_spec(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        task = add_collection_task(session, project_a, user_id)
        session.commit()
    insert_episodes(factory, [episode_row(project_a, user_id, task_id=task)])
    assert _coverage(factory, project_a, user_id, "ACTION") == []


@pytest.mark.parametrize(
    "actions",
    [
        [],
        # 元素取不出标识：既无 code 也无可用的 name。
        [{"description": "未命名动作"}],
        [{"code": ""}],
        # 非对象元素：SQLite 下若不先判类型会直接报 malformed JSON。
        ["pick"],
    ],
)
def test_action_unknown_when_action_definition_unidentifiable(closed_db, actions) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        spec = add_action_spec(session, project_a, user_id, actions=actions)
        task = add_collection_task(session, project_a, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(factory, [episode_row(project_a, user_id, task_id=task)])
    assert _coverage(factory, project_a, user_id, "ACTION") == []


# --------------------------------------------------------------------------- #
# LABEL
# --------------------------------------------------------------------------- #
def _approve(closed_db, labels_by_episode: dict[UUID, list[dict[str, object]]]) -> None:
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        approve_episodes(
            session,
            project_a,
            user_id,
            labels_by_episode=labels_by_episode,
        )
        session.commit()


def test_label_counts_approved_records_per_label_key(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    episodes = [
        episode_row(project_a, user_id),
        episode_row(project_a, user_id),
    ]
    insert_episodes(factory, episodes)
    _approve(
        closed_db,
        {
            episodes[0].id: [{"label_key": "grasp", "value": True}, {"label_key": "place"}],
            episodes[1].id: [{"label_key": "grasp", "value": False}],
        },
    )
    entries = _coverage(factory, project_a, user_id, "LABEL")
    assert _counts(entries) == {"grasp": 2, "place": 1}


def test_label_zero_fills_missing_label_key_when_all_annotated(closed_db) -> None:
    factory, project_a, _project_b, user_id = closed_db
    episode = episode_row(project_a, user_id)
    insert_episodes(factory, [episode])
    _approve(closed_db, {episode.id: [{"label_key": "grasp"}]})
    entries = _coverage(
        factory,
        project_a,
        user_id,
        "LABEL",
        wanted={("LABEL", "grasp-cup")},
    )
    missing = next(item for item in entries if item.dimension_value == "grasp-cup")
    assert missing.count == 0
    # 标注投影会 bump Episode.updated_at，这里只要求有可信时间戳（新鲜度可用）。
    assert missing.measured_at is not None


def test_label_unknown_when_any_episode_is_unannotated(closed_db) -> None:
    """未标注的 Episode 是"还不知道有没有这个标签"，不是"没有"。"""
    factory, project_a, _project_b, user_id = closed_db
    annotated = episode_row(project_a, user_id)
    insert_episodes(factory, [annotated, episode_row(project_a, user_id)])
    _approve(closed_db, {annotated.id: [{"label_key": "grasp"}]})
    assert _coverage(factory, project_a, user_id, "LABEL", wanted={("LABEL", "grasp")}) == []


def test_label_unknown_when_annotation_status_drifts_from_records(closed_db) -> None:
    """读模型标 APPROVED 但没有批准记录：宁可 UNKNOWN，也不能报空标签集。"""
    factory, project_a, _project_b, user_id = closed_db
    insert_episodes(
        factory,
        [episode_row(project_a, user_id, annotation_status="APPROVED")],
    )
    assert _coverage(factory, project_a, user_id, "LABEL", wanted={("LABEL", "grasp")}) == []


# --------------------------------------------------------------------------- #
# 端口级口径
# --------------------------------------------------------------------------- #
def test_zero_fill_never_applies_to_open_axes(closed_db) -> None:
    """SCENE 缺值仍是 UNKNOWN：开放轴不因总体完整就补 0。"""
    factory, project_a, _project_b, user_id = closed_db
    with factory() as session:
        robot = add_robot(session, project_a, user_id)
        rgb = add_sensor(session, project_a, user_id, robot_id=robot, sensor_type="RGB")
        session.commit()
    insert_episodes(
        factory,
        [episode_row(project_a, user_id, scene_labels=[], sensor_ids=[str(rgb)])],
    )
    assert _coverage(factory, project_a, user_id, "SCENE", wanted={("SCENE", "kitchen")}) == []
