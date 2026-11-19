"""迁移测试：0065 为 processing_runs 增加失败收口时间 closed_at。

覆盖 SQLite upgrade → downgrade → upgrade 往返、存量行不回填、错误原因保留，
以及配置了真实 PostgreSQL 时的同一往返。0065 只 ADD COLUMN，不重建表。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "processing_runs"
COLUMN = "closed_at"
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
    # ConfigParser 的 % 插值：PG 的 ?options=...%3D... 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(db_path: Path) -> Engine:
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _columns(engine: Engine) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspect(engine).get_columns(TABLE)}


def _seed_failed_run(connection: object) -> tuple[str, str]:
    """在 0064 的表结构上写一条 FAILED 运行（不含 closed_at 列）。"""
    project_id, user_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (project_id, f"p-{project_id[:6]}", "项目"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user_id, "用户"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO processing_runs "
        "(id, project_id, pipeline_version, input_refs, status, stage, attempt, "
        "started_at, finished_at, error_code, error_summary, created_by, updated_by) "
        "VALUES (?, ?, 'pipeline-v1', '[\"raw/1\"]', 'FAILED', 'FILE_VALIDATION', 1, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'parse_error', '字段缺失', ?, ?)",
        (run_id, project_id, user_id, user_id),
    )
    return run_id, user_id


def test_closed_at_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "closed_at.db"
    config = _config(db_path)

    command.upgrade(config, "0064")
    engine = _engine(db_path)
    try:
        assert COLUMN not in _columns(engine)
        with engine.begin() as connection:
            run_id, _user_id = _seed_failed_run(connection)
    finally:
        engine.dispose()

    # 升级：新列可空，存量 FAILED 行不被回填/改写成已收口。
    command.upgrade(config, "0065")
    engine = _engine(db_path)
    try:
        column = _columns(engine)[COLUMN]
        assert column["nullable"] is True
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, error_code, error_summary, closed_at "
                    "FROM processing_runs WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
        assert row.status == "FAILED"
        assert row.error_code == "parse_error"
        assert row.error_summary == "字段缺失"
        assert row.closed_at is None
    finally:
        engine.dispose()

    # 回滚：只删列，行与错误原因保留，运行回到“FAILED 仍可重试”的旧语义。
    command.downgrade(config, "0064")
    engine = _engine(db_path)
    try:
        assert COLUMN not in _columns(engine)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, error_code, error_summary "
                    "FROM processing_runs WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
        assert row.status == "FAILED"
        assert row.error_code == "parse_error"
    finally:
        engine.dispose()

    # 再次升级：列恢复且可写入/读回收口时间。
    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        assert COLUMN in _columns(engine)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE processing_runs SET closed_at = CURRENT_TIMESTAMP " "WHERE id = :id"),
                {"id": run_id},
            )
        with engine.connect() as connection:
            value = connection.execute(
                text("SELECT closed_at FROM processing_runs WHERE id = :id"),
                {"id": run_id},
            ).scalar_one()
        assert value is not None
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_closed_at_roundtrip_on_postgresql() -> None:
    schema = f"ddp_processing_closed_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0064")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
            project_id, user_id, run_id = uuid4(), uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                    {"id": project_id, "code": schema[-20:], "name": "PG 项目"},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
                    {"id": user_id, "name": "PG 用户"},
                )
                connection.execute(
                    text(
                        "INSERT INTO processing_runs "
                        "(id, project_id, pipeline_version, input_refs, status, stage, "
                        "attempt, started_at, finished_at, error_code, error_summary, "
                        "created_by, updated_by) VALUES "
                        "(:id, :project_id, 'pipeline-v1', '[\"raw/1\"]', 'FAILED', "
                        "'FILE_VALIDATION', 1, now(), now(), 'parse_error', '字段缺失', "
                        ":user_id, :user_id)"
                    ),
                    {"id": run_id, "project_id": project_id, "user_id": user_id},
                )
        finally:
            engine.dispose()

        command.upgrade(config, "0065")
        engine = create_engine(url)
        try:
            assert _columns(engine)[COLUMN]["nullable"] is True
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE processing_runs SET closed_at = now() WHERE id = :id"),
                    {"id": run_id},
                )
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT error_code, closed_at FROM processing_runs " "WHERE id = :id"),
                    {"id": run_id},
                ).one()
            assert row.error_code == "parse_error"
            assert row.closed_at is not None
        finally:
            engine.dispose()

        command.downgrade(config, "0064")
        engine = create_engine(url)
        try:
            assert COLUMN not in _columns(engine)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM processing_runs WHERE id = :id"),
                        {"id": run_id},
                    ).scalar_one()
                    == 1
                )
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
