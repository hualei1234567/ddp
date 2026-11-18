"""迁移测试：0059 放开 ``data_lineage_edges.relation`` 的 ``TRAINED_ON``。

**为什么单独测这条迁移**

``TRAINED_ON`` 是 ADR-0036 冻结的 ``DatasetVersion → TrainingRun`` 血缘关系。
关系只存在于 Python 枚举里而数据库 CHECK 没放开，写边会在提交时被拒绝；反过来，
回滚若静默删掉新关系，会破坏"血缘只追加"的承诺。这里显式验证：

- 升级后存量边（旧关系）原样保留；
- 升级后 ``TRAINED_ON`` 可写，未知关系仍被 CHECK 拒绝；
- 已产生 ``TRAINED_ON`` 边时回滚被显式拒绝，而不是删历史；
- 没有新边时可正常回滚到 0057，旧关系集合恢复，并可再升级；
- 配置 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再走一遍往返。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "data_lineage_edges"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_REVISION_BEFORE = "0057"
_LEGACY_RELATION = "INCLUDED_IN"
_NEW_RELATION = "TRAINED_ON"

_NODE_INSERT = text(
    "INSERT INTO lineage_node_refs (project_id, node_type, node_id, is_active) "
    "VALUES (:project_id, :node_type, :node_id, :is_active)"
)
_EDGE_INSERT = text(
    "INSERT INTO data_lineage_edges ("
    "id, project_id, from_node_type, from_node_id, to_node_type, to_node_id,"
    " relation, metadata, created_by"
    ") VALUES ("
    ":id, :project_id, :from_type, :from_id, :to_type, :to_id,"
    " :relation, '{}', :user_id"
    ")"
)


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 插值：PG 的 ``?options=-csearch_path%3D...`` 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _config(db_path: Path) -> Config:
    return _config_for_url(f"sqlite+pysqlite:///{db_path}")


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _seed(
    connection,
    *,
    project_id: str,
    user_id: str,
    version_id: str,
    episode_id: str,
    run_id: str,
) -> None:
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
        {"id": project_id, "code": f"p-{project_id[:6]}", "name": "项目"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
        {"id": user_id, "name": "用户"},
    )
    for node_type, node_id in (
        ("DATASET_VERSION", version_id),
        ("EPISODE", episode_id),
        ("TRAINING_RUN", run_id),
    ):
        connection.execute(
            _NODE_INSERT,
            {
                "project_id": project_id,
                "node_type": node_type,
                "node_id": node_id,
                "is_active": True,
            },
        )


def _insert_edge(
    connection,
    *,
    project_id: str,
    user_id: str,
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: str,
    relation: str,
) -> None:
    connection.execute(
        _EDGE_INSERT,
        {
            "id": uuid4().hex,
            "project_id": project_id,
            "from_type": from_type,
            "from_id": from_id,
            "to_type": to_type,
            "to_id": to_id,
            "relation": relation,
            "user_id": user_id,
        },
    )


def _count_edges(engine: Engine, relation: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE relation = :relation"),  # noqa: S608
            {"relation": relation},
        ).scalar_one()


def test_upgrade_keeps_legacy_rows_and_allows_trained_on(tmp_path: Path) -> None:
    db_path = tmp_path / "training_lineage.db"
    config = _config(db_path)
    command.upgrade(config, _REVISION_BEFORE)
    project_id, user_id = uuid4().hex, uuid4().hex
    version_id, episode_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            _seed(
                connection,
                project_id=project_id,
                user_id=user_id,
                version_id=version_id,
                episode_id=episode_id,
                run_id=run_id,
            )
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="EPISODE",
                from_id=episode_id,
                to_type="DATASET_VERSION",
                to_id=version_id,
                relation=_LEGACY_RELATION,
            )

        command.upgrade(config, "head")
        assert _count_edges(engine, _LEGACY_RELATION) == 1
        # 重建表不会丢索引。
        assert "ix_data_lineage_edges_project_from" in {
            index["name"] for index in inspect(engine).get_indexes(TABLE)
        }

        with engine.begin() as connection:
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="DATASET_VERSION",
                from_id=version_id,
                to_type="TRAINING_RUN",
                to_id=run_id,
                relation=_NEW_RELATION,
            )
        assert _count_edges(engine, _NEW_RELATION) == 1

        with engine.begin() as connection, pytest.raises(IntegrityError):
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="DATASET_VERSION",
                from_id=version_id,
                to_type="TRAINING_RUN",
                to_id=run_id,
                relation="BOGUS_RELATION",
            )

        # 重建表不得丢掉"只追加"触发器：删除历史边必须被拒绝。
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(
                text(f"DELETE FROM {TABLE} WHERE relation = :relation"),  # noqa: S608
                {"relation": _LEGACY_RELATION},
            )
    finally:
        engine.dispose()


def test_downgrade_refuses_to_drop_history(tmp_path: Path) -> None:
    db_path = tmp_path / "training_lineage_guard.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    project_id, user_id = uuid4().hex, uuid4().hex
    version_id, episode_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            _seed(
                connection,
                project_id=project_id,
                user_id=user_id,
                version_id=version_id,
                episode_id=episode_id,
                run_id=run_id,
            )
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="EPISODE",
                from_id=episode_id,
                to_type="DATASET_VERSION",
                to_id=version_id,
                relation=_LEGACY_RELATION,
            )
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="DATASET_VERSION",
                from_id=version_id,
                to_type="TRAINING_RUN",
                to_id=run_id,
                relation=_NEW_RELATION,
            )

        # 有新增血缘时拒绝回滚，且不得删除任何一行（血缘只追加）。
        with pytest.raises(RuntimeError, match="TRAINED_ON"):
            command.downgrade(config, _REVISION_BEFORE)
        assert _count_edges(engine, _LEGACY_RELATION) == 1
        assert _count_edges(engine, _NEW_RELATION) == 1
    finally:
        engine.dispose()


def test_downgrade_restores_old_relation_set_and_reupgrades(tmp_path: Path) -> None:
    db_path = tmp_path / "training_lineage_down.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    project_id, user_id = uuid4().hex, uuid4().hex
    version_id, episode_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            _seed(
                connection,
                project_id=project_id,
                user_id=user_id,
                version_id=version_id,
                episode_id=episode_id,
                run_id=run_id,
            )
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="EPISODE",
                from_id=episode_id,
                to_type="DATASET_VERSION",
                to_id=version_id,
                relation=_LEGACY_RELATION,
            )

        # 没有 TRAINED_ON 边时可正常回滚；存量边不受影响。
        command.downgrade(config, _REVISION_BEFORE)
        assert _count_edges(engine, _LEGACY_RELATION) == 1

        with engine.begin() as connection, pytest.raises(IntegrityError):
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="DATASET_VERSION",
                from_id=version_id,
                to_type="TRAINING_RUN",
                to_id=run_id,
                relation=_NEW_RELATION,
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            _insert_edge(
                connection,
                project_id=project_id,
                user_id=user_id,
                from_type="DATASET_VERSION",
                from_id=version_id,
                to_type="TRAINING_RUN",
                to_id=run_id,
                relation=_NEW_RELATION,
            )
        assert _count_edges(engine, _NEW_RELATION) == 1
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    schema = f"ddp_training_lineage_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, _REVISION_BEFORE)
        engine = create_engine(url)
        project_id, user_id = uuid4(), uuid4()
        version_id, episode_id, run_id = uuid4(), uuid4(), uuid4()
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                    {"id": project_id, "code": schema[-20:], "name": "PG 项目"},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
                    {"id": user_id, "name": "PG 用户"},
                )
                for node_type, node_id in (
                    ("DATASET_VERSION", version_id),
                    ("EPISODE", episode_id),
                    ("TRAINING_RUN", run_id),
                ):
                    connection.execute(
                        _NODE_INSERT,
                        {
                            "project_id": project_id,
                            "node_type": node_type,
                            "node_id": node_id,
                            "is_active": True,
                        },
                    )
                _insert_edge(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    from_type="EPISODE",
                    from_id=episode_id,
                    to_type="DATASET_VERSION",
                    to_id=version_id,
                    relation=_LEGACY_RELATION,
                )
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert _count_edges(engine, _LEGACY_RELATION) == 1
            # 先在保存点里验证 TRAINED_ON 可写，再回滚保存点，保持降级前提。
            with engine.begin() as connection:
                savepoint = connection.begin_nested()
                _insert_edge(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    from_type="DATASET_VERSION",
                    from_id=version_id,
                    to_type="TRAINING_RUN",
                    to_id=run_id,
                    relation=_NEW_RELATION,
                )
                savepoint.rollback()
            with engine.begin() as connection:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    _insert_edge(
                        connection,
                        project_id=project_id,
                        user_id=user_id,
                        from_type="DATASET_VERSION",
                        from_id=version_id,
                        to_type="TRAINING_RUN",
                        to_id=run_id,
                        relation="BOGUS_RELATION",
                    )
            # 重建 CHECK 后守卫必须仍然存在：血缘边不得被删除。
            with engine.begin() as connection:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(f"DELETE FROM {TABLE} WHERE relation = :relation"),  # noqa: S608
                        {"relation": _LEGACY_RELATION},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, _REVISION_BEFORE)
        engine = create_engine(url)
        try:
            assert _count_edges(engine, _LEGACY_RELATION) == 1
            with engine.begin() as connection:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    _insert_edge(
                        connection,
                        project_id=project_id,
                        user_id=user_id,
                        from_type="DATASET_VERSION",
                        from_id=version_id,
                        to_type="TRAINING_RUN",
                        to_id=run_id,
                        relation=_NEW_RELATION,
                    )
        finally:
            engine.dispose()

        command.upgrade(config, "head")
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
