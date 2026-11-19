"""迁移测试：0067 为外部训练引用新增 DRY_RUN 状态与 dry_run_plan 列。

覆盖 SQLite upgrade → 行为测试 → downgrade → 约束测试 → upgrade 往返，以及配置
真实 PostgreSQL 时的同一往返。0067 通过 batch 重建表并重装训练守卫。
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
TABLE = "external_training_references"
COLUMN = "dry_run_plan"
_STATUS_CONSTRAINT = "ck_external_training_references_external_status_allowed"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(url: str) -> Engine:
    # 迁移只验证表结构/约束/触发器，不验证外键：测试行用占位 UUID。
    return create_engine(url)


def _columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(TABLE)}


def _check_sql(engine: Engine, name: str) -> str:
    for item in inspect(engine).get_check_constraints(TABLE):
        if item.get("name") == name:
            return str(item.get("sqltext") or "").upper()
    return ""


_INSERT_SQL = (
    "INSERT INTO external_training_references "
    "(id, project_id, adapter_id, adapter_version, training_delivery_id, "
    "training_request_revision_id, dataset_version_id, external_run_id, "
    "external_status, execution_mode, submitted_at, last_synced_at, "
    "correlation_id, idempotency_key, created_by, updated_by) "
    "VALUES (:id, :project_id, 'adapter', '1.0.0', :delivery_id, :revision_id, "
    ":version_id, :run_id, :status, :mode, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
    ":corr, :key, :user, :user)"
)


def _insert_params(*, status: str = "SUBMITTED", mode: str = "MOCK") -> dict[str, str]:
    return {
        "id": uuid4().hex,
        "project_id": uuid4().hex,
        "delivery_id": uuid4().hex,
        "revision_id": uuid4().hex,
        "version_id": uuid4().hex,
        "run_id": f"run-{uuid4().hex[:12]}",
        "status": status,
        "mode": mode,
        "corr": f"corr-{uuid4().hex[:12]}",
        "key": f"key-{uuid4().hex[:20]}",
        "user": uuid4().hex,
    }


def test_dry_run_status_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "dry_run.db"
    config = _config(db_path)

    # 注：0019 直接用运行时 ORM 建表，全新库在 0065 处已可能带当前模型的新列；
    # 因此这里不断言"升级前不存在"，只验证 0067 的升降级与约束/触发器行为。
    command.upgrade(config, "0067")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN in _columns(engine)
        status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
        assert "SUBMITTED" in status_sql and "DRY_RUN" in status_sql
        # 演练是终态：迁移后不可再推进。
        with engine.connect() as connection:
            params = _insert_params()
            try:
                connection.execute(text(_INSERT_SQL), params)
                connection.execute(
                    text(
                        "UPDATE external_training_references "
                        "SET external_status='DRY_RUN' WHERE id=:id"
                    ),
                    {"id": params["id"]},
                )
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "UPDATE external_training_references "
                            "SET external_status='SUCCEEDED' WHERE id=:id"
                        ),
                        {"id": params["id"]},
                    )
            finally:
                connection.rollback()

        # 演练计划只能挂在 DRY_RUN 引用上。
        with engine.connect() as connection:
            params = _insert_params(mode="MOCK")
            try:
                connection.execute(text(_INSERT_SQL), params)
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "UPDATE external_training_references "
                            "SET dry_run_plan='{\"dry_run\": true}' WHERE id=:id"
                        ),
                        {"id": params["id"]},
                    )
            finally:
                connection.rollback()
    finally:
        engine.dispose()

    # 回滚：删列并恢复不含 DRY_RUN 的状态约束。
    command.downgrade(config, "0065")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN not in _columns(engine)
        status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
        assert "SUBMITTED" in status_sql and "DRY_RUN" not in status_sql
        with engine.connect() as connection:
            params = _insert_params()
            try:
                connection.execute(text(_INSERT_SQL), params)
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "UPDATE external_training_references "
                            "SET external_status='DRY_RUN' WHERE id=:id"
                        ),
                        {"id": params["id"]},
                    )
            finally:
                connection.rollback()
    finally:
        engine.dispose()

    # 再次升级：列与状态恢复。
    command.upgrade(config, "head")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert COLUMN in _columns(engine)
        status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
        assert "SUBMITTED" in status_sql and "DRY_RUN" in status_sql
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_dry_run_status_roundtrip_on_postgresql() -> None:
    schema = f"ddp_training_dry_run_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0067")
        engine = create_engine(url)
        try:
            assert COLUMN in _columns(engine)
            status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
            assert "SUBMITTED" in status_sql and "DRY_RUN" in status_sql
            # 触发器先于外键校验：直接插入 DRY_RUN 初始状态必须被拒。
            with engine.connect() as connection:
                transaction = connection.begin()
                with pytest.raises(IntegrityError):
                    connection.execute(text(_INSERT_SQL), _insert_params(status="DRY_RUN"))
                transaction.rollback()
        finally:
            engine.dispose()

        command.downgrade(config, "0065")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
            status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
            assert "SUBMITTED" in status_sql and "DRY_RUN" not in status_sql
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert COLUMN in _columns(engine)
            status_sql = _check_sql(engine, _STATUS_CONSTRAINT)
            assert "SUBMITTED" in status_sql and "DRY_RUN" in status_sql
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
