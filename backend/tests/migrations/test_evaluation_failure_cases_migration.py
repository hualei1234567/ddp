"""迁移 0061：evaluations.failure_cases 在 SQLite/PostgreSQL 上升降级可逆。

要证明的不是"列加上了"，而是：

1. **全新库路径幂等**：0021 用活的 ORM 建 ``evaluations``，0061 必须识别
   "列已存在"并空操作，否则全新库 ``duplicate column``；
2. **存量路径真的回填空数组**：迁移前的行没有失败事实，得到 ``[]`` 而不是
   NULL，也不是伪造的示例数据；
3. **守卫触发器重装**：评测成功后失败明细必须不可改；存量库的旧触发器正文
   不含新列，0061 要拆装一轮；
4. **回滚不留下无守卫表**，且再次升级成功。
"""

from __future__ import annotations

# ruff: noqa: S608 - 被测对象是迁移的 DDL/DML；表名与列名来自本模块常量，
# 参数一律走绑定变量，不接收外部输入（与 0043 迁移测试同口径）。
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.modelops_guards import (
    drop_modelops_guards,
    install_modelops_guards,
)
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "evaluations"
COLUMN = "failure_cases"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
EVALUATION_TRIGGERS = {
    "trg_evaluations_initial_status",
    "trg_evaluations_transition",
    "trg_evaluations_no_delete",
    "trg_evaluations_immutable",
}
_THRESHOLDS_JSON = '{"success_rate": {"min": 0.9}}'


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 的 % 插值：PG 的 ?options=...%3D... 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _config(db_path: Path) -> Config:
    return _config_for_url(f"sqlite+pysqlite:///{db_path}")


def _sqlite_engine(db_path: Path):
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(TABLE)}


def _trigger_sql(engine) -> str:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='trigger' AND name='trg_evaluations_immutable'"
                )
            ).scalar()
            or ""
        )


def _seed_evaluation(db_path_or_engine) -> dict[str, str]:
    """直接落库造一条评测行（含外键链），刻意不走应用层。

    存量行是旧代码写进去的；用新代码造数据就测不到"迁移如何对待历史行"。
    """
    ids = {
        "project": uuid4().hex,
        "user": uuid4().hex,
        "model": uuid4().hex,
        "version": uuid4().hex,
        "dataset_version": uuid4().hex,
        "row": uuid4().hex,
    }
    engine = (
        db_path_or_engine
        if hasattr(db_path_or_engine, "begin")
        else _sqlite_engine(db_path_or_engine)
    )
    owns_engine = engine is not db_path_or_engine
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                {"id": ids["project"], "code": f"p-{ids['project'][:6]}", "name": "项目"},
            )
            connection.execute(
                text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
                {"id": ids["user"], "name": "用户"},
            )
            connection.execute(
                text(
                    "INSERT INTO models (id, project_id, code, name, task_type, framework, "
                    "owner_id, status, tags, version, created_at, updated_at) "
                    "VALUES (:id, :project, 'm-1', '模型', 'IMITATION', 'pytorch', "
                    ":user, 'DRAFT', '[]', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": ids["model"],
                    "project": ids["project"],
                    "user": ids["user"],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO model_versions (id, project_id, model_id, version_no, "
                    "status, artifact, runtime, artifact_sha256, dataset_version_ids, "
                    "metrics_summary, version, created_at, updated_at) VALUES "
                    "(:id, :project, :model, 1, 'DRAFT', '{}', '{}', :sha, '[]', '{}', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": ids["version"],
                    "project": ids["project"],
                    "model": ids["model"],
                    "sha": "sha256:" + "a" * 64,
                },
            )
            connection.execute(
                text(
                    f"INSERT INTO {TABLE} (id, project_id, model_version_id, "
                    "dataset_version_id, status, metrics, thresholds, verdict, version, "
                    "created_at, updated_at) VALUES (:id, :project, :version, "
                    f":dataset_version, 'QUEUED', '{{}}', '{_THRESHOLDS_JSON}', 'UNKNOWN', 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": ids["row"],
                    "project": ids["project"],
                    "version": ids["version"],
                    "dataset_version": ids["dataset_version"],
                },
            )
    finally:
        if owns_engine:
            engine.dispose()
    return ids


def _downgrade_to_pre_0061_shape(engine) -> None:
    """把 evaluations 还原成 0057 时的形状：没有 failure_cases。

    当前 ORM 已含新列，0021 在全新库上会自动建出它；模拟"升级前就已存在的库"
    必须显式删列。顺序陷阱：删列前先拆守卫，否则 SQLite/PG 的触发器正文
    引用该列会让 DROP COLUMN 失败。
    """
    with engine.begin() as connection:
        assert COLUMN in {
            item["name"] for item in inspect(connection).get_columns(TABLE)
        }, "前置条件不成立：当前 ORM 未包含 failure_cases"
        drop_modelops_guards(connection)
        connection.exec_driver_sql(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
        install_modelops_guards(connection)


def test_fresh_database_has_column_default_and_guard(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    command.upgrade(_config(db_path), "head")

    engine = _sqlite_engine(db_path)
    try:
        columns = {item["name"]: item for item in inspect(engine).get_columns(TABLE)}
        assert COLUMN in columns
        assert columns[COLUMN]["nullable"] is False
        assert COLUMN in _trigger_sql(engine)
        triggers = {
            row[0]
            for row in engine.connect().execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"),
                {"t": TABLE},
            )
        }
        assert EVALUATION_TRIGGERS <= triggers
    finally:
        engine.dispose()


def test_existing_database_gains_column_with_empty_array(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    config = _config(db_path)
    command.upgrade(config, "0057")

    engine = _sqlite_engine(db_path)
    try:
        _downgrade_to_pre_0061_shape(engine)
        ids = _seed_evaluation(engine)
        assert COLUMN not in _columns(engine)
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = _sqlite_engine(db_path)
    try:
        assert COLUMN in _columns(engine)
        with engine.connect() as connection:
            value = connection.execute(
                text(f"SELECT {COLUMN} FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
            ).scalar()
        assert json.loads(value) == []
        assert COLUMN in _trigger_sql(engine)
    finally:
        engine.dispose()


def test_downgrade_roundtrip_keeps_guards_and_data(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = _sqlite_engine(db_path)
    try:
        ids = _seed_evaluation(engine)
        assert COLUMN in _columns(engine)
    finally:
        engine.dispose()

    command.downgrade(config, "0057")
    engine = _sqlite_engine(db_path)
    try:
        assert COLUMN not in _columns(engine)
        assert COLUMN not in _trigger_sql(engine), "回滚后触发器不得引用已删列"
        triggers = {
            row[0]
            for row in engine.connect().execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"),
                {"t": TABLE},
            )
        }
        assert EVALUATION_TRIGGERS <= triggers, "回滚不得留下无守卫的表"
        with engine.connect() as connection:
            count = connection.execute(
                text(f"SELECT COUNT(*) FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
            ).scalar()
        assert count == 1
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _sqlite_engine(db_path)
    try:
        assert COLUMN in _columns(engine)
        with engine.connect() as connection:
            value = connection.execute(
                text(f"SELECT {COLUMN} FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
            ).scalar()
        assert json.loads(value) == []
        assert COLUMN in _trigger_sql(engine)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    schema = f"ddp_eval_failure_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        # 先到 0057，删掉 0021 按当前 ORM 建出的列，模拟存量库。
        command.upgrade(config, "0057")
        engine = create_engine(url)
        try:
            _downgrade_to_pre_0061_shape(engine)
            ids = _seed_evaluation(engine)
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            columns = {item["name"]: item for item in inspect(engine).get_columns(TABLE)}
            assert COLUMN in columns
            assert columns[COLUMN]["nullable"] is False
            with engine.connect() as connection:
                value = connection.execute(
                    text(f"SELECT {COLUMN} FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
                ).scalar()
                assert value == []
                source = connection.execute(
                    text(
                        "SELECT prosrc FROM pg_proc WHERE proname = "
                        "'ddp_evaluations_immutable_guard' AND pronamespace = "
                        "(SELECT oid FROM pg_namespace WHERE nspname = :schema)"
                    ),
                    {"schema": schema},
                ).scalar()
            assert COLUMN in source
        finally:
            engine.dispose()

        command.downgrade(config, "0057")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
            with engine.connect() as connection:
                count = connection.execute(
                    text(f"SELECT COUNT(*) FROM {TABLE} WHERE id=:id"), {"id": ids["row"]}
                ).scalar()
                assert count == 1
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert COLUMN in _columns(engine)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
