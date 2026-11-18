"""迁移 0063：训练引用的 ``config_summary`` / ``code_version`` / ``model_version``。

**为什么单独测这条迁移**

这三列是 N7-002 的全部落库面：配置摘要回填成"看起来有配置"、版本标识回填成
猜测值、或回滚删不干净，都会让训练 run 的可追溯性变成假象。这里显式验证：

- 升级后三列存在且可空（存量保持 NULL，不猜测）；
- 升级前已存在的引用行拿到 NULL，新写入的摘要可读回；
- 回滚把三列删干净，且可反复升降级；
- 真实 PostgreSQL 上同样可升降级（未配置 ``DDP_TEST_POSTGRES_URL`` 时跳过）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql

BACKEND_DIR = Path(__file__).resolve().parents[2]
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
TABLE = "external_training_references"
_NEW_COLUMNS = ("config_summary", "code_version", "model_version")

_REF_INSERT = text(
    "INSERT INTO external_training_references ("
    "id, project_id, adapter_id, adapter_version, training_delivery_id,"
    " training_request_revision_id, dataset_version_id, external_run_id,"
    " external_status, execution_mode, status_stale, submitted_at, last_synced_at,"
    " correlation_id, idempotency_key, version, created_at, updated_at,"
    " created_by, updated_by"
    ") VALUES ("
    ":id, :project_id, 'local-training', '1.0.0', :delivery_id,"
    " :revision_id, :version_id, :run_id,"
    " 'SUBMITTED', 'MOCK', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    "'corr-0063', :idempotency_key, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    " :actor_id, :actor_id"
    ")"
)


def _config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def _columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(TABLE)}


def _trigger_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:table"),
                {"table": TABLE},
            )
        }


def _insert_reference(connection, *, suffix: str) -> None:
    connection.execute(
        _REF_INSERT,
        {
            "id": f"{suffix:0>32}",
            "project_id": "0" * 31 + "1",
            "delivery_id": "0" * 31 + "2",
            "revision_id": "0" * 31 + "3",
            "version_id": "0" * 31 + "4",
            "run_id": f"run-{suffix}",
            "idempotency_key": f"idem-{suffix}-key",
            "actor_id": "0" * 31 + "5",
        },
    )


def test_upgrade_keeps_legacy_null_and_persists_new_fields(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "training_run_fields.db")
    config = _config(url)
    command.upgrade(config, "0062")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            # 升级前已存在的引用：迁移无法回溯当时的配置与版本，只能保持 NULL。
            _insert_reference(connection, suffix="a")

        command.upgrade(config, "head")
        columns = _columns(engine)
        for name in _NEW_COLUMNS:
            assert name in columns

        with engine.begin() as connection:
            legacy = connection.execute(
                text(
                    "SELECT config_summary, code_version, model_version"
                    " FROM external_training_references WHERE external_run_id = 'run-a'"
                )
            ).one()
            # 存量必须是 NULL：回填只能靠猜测，等于把推断伪装成事实。
            assert legacy == (None, None, None)

            _insert_reference(connection, suffix="b")
            connection.execute(
                text(
                    "UPDATE external_training_references"
                    " SET config_summary = :summary, code_version = 'git:abc1234',"
                    " model_version = 'policy-v3'"
                    " WHERE external_run_id = 'run-b'"
                ),
                {"summary": json.dumps({"learning_rate": 0.001})},
            )
            stored = connection.execute(
                text(
                    "SELECT config_summary, code_version, model_version"
                    " FROM external_training_references WHERE external_run_id = 'run-b'"
                )
            ).one()
            # SQLite 的 JSON 列以文本保存，读回后解析得到同一对象。
            assert json.loads(stored[0]) == {"learning_rate": 0.001}
            assert stored[1] == "git:abc1234"
            assert stored[2] == "policy-v3"
    finally:
        engine.dispose()


def test_downgrade_removes_columns_and_reupgrades(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "training_run_fields_down.db")
    config = _config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert set(_NEW_COLUMNS) <= _columns(engine)
        triggers_before = _trigger_names(engine)
        assert "trg_external_training_references_no_delete" in triggers_before
    finally:
        engine.dispose()

    command.downgrade(config, "0062")
    engine = create_engine(url)
    try:
        remaining = _columns(engine)
        assert not set(_NEW_COLUMNS) & remaining
        # 既有列不受影响。
        assert {"external_run_id", "artifacts", "execution_mode"} <= remaining
        # SQLite 删列会重建表；训练守卫必须被显式重装，不能静默丢失。
        assert _trigger_names(engine) == triggers_before
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert set(_NEW_COLUMNS) <= _columns(engine)
        assert _trigger_names(engine) == triggers_before
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    """真实 PostgreSQL 上的升级/回滚与 JSONB 列类型（SQLite 不作为验收替代）。"""
    schema = f"ddp_train_run_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config(url)

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            columns = {column["name"]: column for column in inspect(engine).get_columns(TABLE)}
            for name in _NEW_COLUMNS:
                assert name in columns
                assert columns[name]["nullable"] is True
            assert isinstance(columns["config_summary"]["type"], postgresql.JSONB)
        finally:
            engine.dispose()

        command.downgrade(config, "0062")
        engine = create_engine(url)
        try:
            remaining = _columns(engine)
            assert not set(_NEW_COLUMNS) & remaining
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert set(_NEW_COLUMNS) <= _columns(engine)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
