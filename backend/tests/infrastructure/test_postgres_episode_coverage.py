"""PostgreSQL 缺口覆盖统计回归（FBK-002）。

在真实 PostgreSQL 上验证 ``jsonb_array_elements_text`` 分支真的能跑通：
SQLite 的 ``json_each`` 用例证明不了 PG 的 JSONB 横向展开语法。
未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_session_factory
from app.infrastructure.repositories.episode_coverage import SqlEpisodeCoverageDirectory
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from tests.infrastructure.annotation_chain_support import approve_episodes
from tests.infrastructure.coverage_support import (
    add_action_spec,
    add_collection_task,
    add_robot,
    add_sensor,
    episode_row,
    insert_episodes,
)

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_coverage_it_{uuid4().hex}"
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


def _seed(engine: Engine) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    factory = create_session_factory(engine)
    project_a, project_b, user_id = uuid4(), uuid4(), uuid4()
    robot_a, device_a = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code=f"pg-a-{project_a.hex[:8]}", name="PG 项目 A"),
                Project(id=project_b, code=f"pg-b-{project_b.hex[:8]}", name="PG 项目 B"),
                User(id=user_id, display_name="PG 用户"),
            ]
        )
        session.flush()
        rows = [
            _episode(project_a, user_id, ["kitchen"], robot_id=robot_a, device_ids=[str(device_a)]),
            _episode(project_a, user_id, ["kitchen", "warehouse"], robot_id=robot_a),
            _episode(project_b, user_id, ["kitchen"]),
        ]
        session.add_all(rows)
        session.flush()
        for row in rows:
            _advance(session, row)
        session.commit()
    return project_a, project_b, user_id, robot_a, device_a


def _episode(
    project_id: UUID,
    user_id: UUID,
    scene_labels: list[str],
    *,
    robot_id: UUID | None = None,
    device_ids: list[str] | None = None,
) -> EpisodeModel:
    return EpisodeModel(
        id=uuid4(),
        project_id=project_id,
        episode_lineage_id=uuid4(),
        episode_version=1,
        code=f"PG-EP-{uuid4().hex[:12]}",
        status="PENDING",
        quality_status="NOT_CHECKED",
        annotation_status="NOT_STARTED",
        robot_id=robot_id,
        device_ids=device_ids or [],
        scene_labels=scene_labels,
        start_time_ns=1_000,
        end_time_ns=2_000,
        duration_ns=1_000,
        time_domain="UTC",
        time_base={},
        canonical_schema_version="ddp.canonical/1.0.0-draft",
        pipeline_id="parser",
        pipeline_version="1.0.0",
        segmentation_rule_version="seg-manual-v1",
        attempt=1,
        max_attempts=3,
        created_at=NOW,
        updated_at=NOW,
        created_by=user_id,
        updated_by=user_id,
    )


def _advance(session: Session, episode: EpisodeModel) -> None:
    """沿合法迁移推到 ACCEPTED；updated_at 固定，避免真实时钟漂移。"""
    statement = update(EpisodeModel).where(EpisodeModel.id == episode.id)

    def step(**values: object) -> None:
        session.execute(statement.values(updated_at=NOW, **values))
        session.flush()

    step(status="PROCESSING")
    step(
        status="READY",
        manifest_key=f"canonical/{episode.project_id}/{episode.id}/1/manifest.json",
        manifest_sha256=SHA,
    )
    step(status="QUALITY_CHECKING")
    step(quality_status="PASS")
    step(status="ACCEPTED")


def _coverage(factory: sessionmaker[Session], project_id: UUID, user_id: UUID, dimensions):
    with factory() as session:
        return SqlEpisodeCoverageDirectory(session).coverage_by_dimension(
            ProjectScope(project_id, actor_id=user_id),
            dimension_types=dimensions,
        )


def test_postgres_jsonb_scene_expansion(postgres_engine: Engine) -> None:
    project_a, _project_b, user_id, _robot, _device = _seed(postgres_engine)
    factory = create_session_factory(postgres_engine)
    entries = _coverage(factory, project_a, user_id, frozenset({"SCENE"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {
        "kitchen": 2,
        "warehouse": 1,
    }
    assert all(
        entry.measured_at is not None and entry.measured_at.tzinfo is not None for entry in entries
    )
    assert all(entry.measured_at == NOW for entry in entries)


def test_postgres_jsonb_device_expansion(postgres_engine: Engine) -> None:
    project_a, _project_b, user_id, _robot, device_a = _seed(postgres_engine)
    factory = create_session_factory(postgres_engine)
    entries = _coverage(factory, project_a, user_id, frozenset({"DEVICE"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {str(device_a): 1}


def test_postgres_project_isolation(postgres_engine: Engine) -> None:
    project_a, project_b, user_id, _robot, _device = _seed(postgres_engine)
    factory = create_session_factory(postgres_engine)
    scene_a = _coverage(factory, project_a, user_id, frozenset({"SCENE"}))
    scene_b = _coverage(factory, project_b, user_id, frozenset({"SCENE"}))
    assert {e.dimension_value: e.count for e in scene_a} == {"kitchen": 2, "warehouse": 1}
    assert {e.dimension_value: e.count for e in scene_b} == {"kitchen": 1}


def test_postgres_measured_at_reflects_latest_update(postgres_engine: Engine) -> None:
    project_a, _project_b, user_id, robot_a, _device = _seed(postgres_engine)
    factory = create_session_factory(postgres_engine)
    newer = NOW + timedelta(hours=2)
    with factory() as session:
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.project_id == project_a, EpisodeModel.robot_id == robot_a)
            .values(updated_at=newer)
        )
        session.commit()
    entries = _coverage(factory, project_a, user_id, frozenset({"ROBOT"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {str(robot_a): 2}
    assert entries[0].measured_at == newer


def test_postgres_sql_uses_native_jsonb_function(postgres_engine: Engine) -> None:
    """确认实际下发的 SQL 真的是服务端展开，而不是取回后在 Python 里数。"""
    project_a, _project_b, user_id, _robot, _device = _seed(postgres_engine)
    factory = create_session_factory(postgres_engine)
    captured: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        captured.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", _capture)
    try:
        _coverage(factory, project_a, user_id, frozenset({"SCENE", "DEVICE"}))
    finally:
        event.remove(postgres_engine, "before_cursor_execute", _capture)
    joined = "\n".join(captured)
    assert "jsonb_array_elements_text" in joined
    # 聚合必须发生在 SQL 里：语句里应出现 GROUP BY 与 count。
    assert "GROUP BY" in joined
    assert "count(" in joined.lower()


# --------------------------------------------------------------------------- #
# 封闭轴：PostgreSQL 分支（jsonb_array_elements / jsonb_typeof / uuid 文本归一）
# --------------------------------------------------------------------------- #
def _seed_closed(engine: Engine) -> tuple[sessionmaker[Session], UUID, UUID]:
    factory = create_session_factory(engine)
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"pg-close-{project_id.hex[:8]}", name="PG 封闭轴"),
                User(id=user_id, display_name="PG 用户"),
            ]
        )
        session.flush()
        session.add(ProjectMember(project_id=project_id, user_id=user_id, role="owner"))
        session.commit()
    return factory, project_id, user_id


def test_postgres_modality_axis_expands_sensor_ids(postgres_engine: Engine) -> None:
    factory, project_id, user_id = _seed_closed(postgres_engine)
    with factory() as session:
        robot = add_robot(session, project_id, user_id)
        rgb = add_sensor(session, project_id, user_id, robot_id=robot, sensor_type="RGB")
        depth = add_sensor(session, project_id, user_id, robot_id=robot, sensor_type="DEPTH")
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_id, user_id, sensor_ids=[str(rgb), str(depth)]),
            episode_row(project_id, user_id, sensor_ids=[str(rgb)]),
        ],
    )
    entries = _coverage(factory, project_id, user_id, frozenset({"MODALITY"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {"RGB": 2, "DEPTH": 1}


def test_postgres_action_axis_expands_referenced_spec_actions(postgres_engine: Engine) -> None:
    factory, project_id, user_id = _seed_closed(postgres_engine)
    with factory() as session:
        spec = add_action_spec(
            session,
            project_id,
            user_id,
            actions=[{"code": "pick"}, {"code": "place"}],
        )
        task = add_collection_task(session, project_id, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(
        factory,
        [
            episode_row(project_id, user_id, task_id=task),
            episode_row(project_id, user_id, task_id=task),
        ],
    )
    entries = _coverage(factory, project_id, user_id, frozenset({"ACTION"}))
    assert {entry.dimension_value: entry.count for entry in entries} == {"pick": 2, "place": 2}


def test_postgres_label_axis_reads_approved_record_labels(postgres_engine: Engine) -> None:
    factory, project_id, user_id = _seed_closed(postgres_engine)
    episode = episode_row(project_id, user_id)
    insert_episodes(factory, [episode])
    with factory() as session:
        approve_episodes(
            session,
            project_id,
            user_id,
            labels_by_episode={episode.id: [{"label_key": "grasp"}]},
        )
        session.commit()
    with factory() as session:
        entries = SqlEpisodeCoverageDirectory(session).coverage_by_dimension(
            ProjectScope(project_id, actor_id=user_id),
            dimension_types=frozenset({"LABEL"}),
            wanted_keys={("LABEL", "grasp-cup")},
        )
    counts = {entry.dimension_value: entry.count for entry in entries}
    assert counts == {"grasp": 1, "grasp-cup": 0}


def test_postgres_closed_axis_sql_uses_jsonb_object_functions(postgres_engine: Engine) -> None:
    factory, project_id, user_id = _seed_closed(postgres_engine)
    with factory() as session:
        spec = add_action_spec(session, project_id, user_id, actions=[{"code": "pick"}])
        task = add_collection_task(session, project_id, user_id, action_spec_id=spec)
        session.commit()
    insert_episodes(factory, [episode_row(project_id, user_id, task_id=task)])
    captured: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        captured.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", _capture)
    try:
        _coverage(factory, project_id, user_id, frozenset({"ACTION"}))
    finally:
        event.remove(postgres_engine, "before_cursor_execute", _capture)
    joined = "\n".join(captured)
    assert "jsonb_array_elements(" in joined
    assert "jsonb_typeof" in joined


def test_postgres_action_axis_works_when_column_is_json_not_jsonb(
    postgres_engine: Engine,
) -> None:
    """迁移建表时 ``action_specs.actions`` 是 ``json``，不是 ``jsonb``。

    这里把列改成与**迁移产物**一致的类型再查一次。

    为什么必须有这条用例：其余 PG 测试都走 ``Base.metadata.create_all``，
    而 ORM 用的是 ``JSON().with_variant(JSONB, "postgresql")``——建出来是
    ``jsonb``。于是「直接对 json 列调 ``jsonb_array_elements``」这种错误在
    测试里永远不会出现，只在真实迁移库上 500。

    实测过一次：真实库 `GET /data-gaps` 返回
    ``function jsonb_array_elements(json) does not exist``。
    修复是在 SQL 里显式 ``cast(... as jsonb)``；本用例锁住它。
    """
    factory, project_id, user_id = _seed_closed(postgres_engine)
    with postgres_engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE action_specs " "ALTER COLUMN actions TYPE json USING actions::json")
        )
        conn.execute(
            text(
                "ALTER TABLE annotation_records " "ALTER COLUMN labels TYPE json USING labels::json"
            )
        )

    with factory() as session:
        spec = add_action_spec(
            session, project_id, user_id, actions=[{"code": "pick"}, {"code": "place"}]
        )
        task = add_collection_task(session, project_id, user_id, action_spec_id=spec)
        session.commit()
    episode = episode_row(project_id, user_id, task_id=task)
    insert_episodes(factory, [episode])
    with factory() as session:
        approve_episodes(
            session,
            project_id,
            user_id,
            labels_by_episode={episode.id: [{"label_key": "grasp"}]},
        )
        session.commit()

    action_entries = _coverage(factory, project_id, user_id, frozenset({"ACTION"}))
    assert {e.dimension_value: e.count for e in action_entries} == {"pick": 1, "place": 1}
    label_entries = _coverage(factory, project_id, user_id, frozenset({"LABEL"}))
    assert {e.dimension_value: e.count for e in label_entries} == {"grasp": 1}
