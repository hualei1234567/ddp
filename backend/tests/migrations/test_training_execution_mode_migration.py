"""迁移 0054：``external_training_references.execution_mode`` 升级/回滚。

**为什么单独测这条迁移**

执行方式是"这次训练在哪里跑"的唯一载体。列没建出来、存量回填成 REMOTE、
或回滚删不干净，都会让 Mock/本地训练重新无法与真实训练区分。这里显式验证：

- 升级后列存在、NOT NULL、默认 ``UNKNOWN``；
- 升级前已存在的存量行拿到 ``UNKNOWN``，而不是 REMOTE；
- CHECK 拒绝非法值；
- 回滚把列删干净，且可反复升降级。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
_REF_INSERT = text(
    "INSERT INTO external_training_references ("
    "id, project_id, adapter_id, adapter_version, training_delivery_id,"
    " training_request_revision_id, dataset_version_id, external_run_id,"
    " external_status, status_stale, submitted_at, last_synced_at,"
    " correlation_id, idempotency_key, version, created_at, updated_at,"
    " created_by, updated_by"
    ") VALUES ("
    ":id, :project_id, 'local-training', '1.0.0', :delivery_id,"
    " :revision_id, :version_id, :run_id,"
    " 'SUBMITTED', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    "'corr-0054', :idempotency_key, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
    " :actor_id, :actor_id"
    ")"
)


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _insert_reference(connection, *, ref_id: str, run_id: str, key: str) -> None:
    connection.execute(
        _REF_INSERT,
        {
            "id": ref_id,
            "project_id": "0" * 31 + "1",
            "delivery_id": "0" * 31 + "2",
            "revision_id": "0" * 31 + "3",
            "version_id": "0" * 31 + "4",
            "run_id": run_id,
            "idempotency_key": key,
            "actor_id": "0" * 31 + "5",
        },
    )


def test_upgrade_backfills_unknown_and_enforces_check(tmp_path: Path) -> None:
    db_path = tmp_path / "execution_mode.db"
    config = _config(db_path)
    command.upgrade(config, "0052")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            # 升级前已存在的行：迁移执行时无法得知当时跑在本地还是远端。
            _insert_reference(conn, ref_id="0" * 31 + "a", run_id="run-legacy", key="idem-legacy")

        command.upgrade(config, "head")
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("external_training_references")
        }
        assert "execution_mode" in columns
        assert columns["execution_mode"]["nullable"] is False
        with engine.begin() as conn:
            legacy = conn.execute(
                text(
                    "SELECT execution_mode FROM external_training_references"
                    " WHERE external_run_id = 'run-legacy'"
                )
            ).scalar_one()
            # 存量必须是 UNKNOWN：不猜测，也不冒充 REMOTE/真实训练。
            assert legacy == "UNKNOWN"
            # 省略该列的 INSERT 必须走默认值。
            _insert_reference(conn, ref_id="0" * 31 + "b", run_id="run-default", key="idem-default")
            defaulted = conn.execute(
                text(
                    "SELECT execution_mode FROM external_training_references"
                    " WHERE external_run_id = 'run-default'"
                )
            ).scalar_one()
            assert defaulted == "UNKNOWN"

        with engine.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE external_training_references"
                    " SET execution_mode = 'BOGUS'"
                    " WHERE external_run_id = 'run-legacy'"
                )
            )
    finally:
        engine.dispose()


def test_downgrade_removes_column_and_reupgrades(tmp_path: Path) -> None:
    db_path = tmp_path / "execution_mode_down.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "execution_mode" in {
            column["name"] for column in inspect(engine).get_columns("external_training_references")
        }
    finally:
        engine.dispose()

    command.downgrade(config, "0052")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "execution_mode" not in {
            column["name"] for column in inspect(engine).get_columns("external_training_references")
        }
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert "execution_mode" in {
            column["name"] for column in inspect(engine).get_columns("external_training_references")
        }
    finally:
        engine.dispose()
