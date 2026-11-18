"""迁移测试：0057 建立 processing_run_steps。

覆盖建表、索引、唯一键、CHECK（跳过必须有原因、失败必须有错误、RUNNING
不得有 finished_at）与双向迁移；配置了 PostgreSQL 时再做一次真实库往返。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "processing_run_steps"
RUNS = "processing_runs"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_START = datetime(2026, 1, 1, tzinfo=UTC)
_FINISH = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)


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
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _seed(connection: object, project_id: str, user_id: str, run_id: str) -> None:
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
        "(id, project_id, pipeline_version, input_refs, status, created_by, updated_by) "
        "VALUES (?, ?, 'pipeline-v1', '[\"raw/1\"]', 'CREATED', ?, ?)",
        (run_id, project_id, user_id, user_id),
    )


def _insert(
    connection: object,
    *,
    project_id: str,
    user_id: str,
    run_id: str,
    stage: str = "FILE_VALIDATION",
    attempt: int = 1,
    status: str = "RUNNING",
    log_summary: str | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    started_at: datetime = _START,
    finished_at: datetime | None = None,
) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO processing_run_steps "
        "(id, project_id, run_id, stage, attempt, status, log_summary, error_code, "
        "error_summary, started_at, finished_at, created_by, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid4().hex,
            project_id,
            run_id,
            stage,
            attempt,
            status,
            log_summary,
            error_code,
            error_summary,
            started_at,
            finished_at,
            user_id,
            user_id,
        ),
    )


def test_upgrade_creates_table_columns_index_and_checks(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.db"
    command.upgrade(_config(db_path), "head")

    engine = _engine(db_path)
    try:
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns(TABLE)}
        assert {
            "id",
            "project_id",
            "run_id",
            "stage",
            "attempt",
            "status",
            "log_summary",
            "error_code",
            "error_summary",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        } <= columns
        indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
        assert "ix_processing_run_steps_project_run_id" in indexes
        uniques = {item["name"] for item in inspector.get_unique_constraints(TABLE)}
        assert "uq_processing_run_steps_run_stage_attempt" in uniques
        checks = {check["name"] for check in inspector.get_check_constraints(TABLE)}
        assert {
            "ck_processing_run_steps_status_allowed",
            "ck_processing_run_steps_stage_allowed",
            "ck_processing_run_steps_skipped_requires_reason",
            "ck_processing_run_steps_failed_requires_error",
            "ck_processing_run_steps_terminal_requires_finished_at",
        } <= checks
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("status", "log_summary", "error_code", "error_summary", "finished_at", "match"),
    [
        ("BOGUS", None, None, None, _FINISH, "status_allowed"),
        ("RUNNING", None, None, None, None, "stage_allowed"),
        ("SKIPPED", None, None, None, _FINISH, "skipped_requires_reason"),
        ("FAILED", None, None, None, _FINISH, "failed_requires_error"),
        ("SUCCEEDED", None, "x", "y", _FINISH, "error_only_when_failed"),
        ("RUNNING", None, None, None, _FINISH, "running_without_finished_at"),
        ("SUCCEEDED", None, None, None, None, "terminal_requires_finished_at"),
    ],
)
def test_database_checks_reject_invalid_rows(
    tmp_path: Path,
    status: str,
    log_summary: str | None,
    error_code: str | None,
    error_summary: str | None,
    finished_at: datetime | None,
    match: str,
) -> None:
    db_path = tmp_path / "checks.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    project_id, user_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    stage = "BOGUS" if match == "stage_allowed" else "FILE_VALIDATION"
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id, run_id)
        with pytest.raises(IntegrityError, match=match):
            with engine.begin() as connection:
                _insert(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    run_id=run_id,
                    stage=stage,
                    status=status,
                    log_summary=log_summary,
                    error_code=error_code,
                    error_summary=error_summary,
                    finished_at=finished_at,
                )
    finally:
        engine.dispose()


def test_duplicate_stage_attempt_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "unique.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    project_id, user_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id, run_id)
            _insert(connection, project_id=project_id, user_id=user_id, run_id=run_id)
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                _insert(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    run_id=run_id,
                    attempt=1,
                )
    finally:
        engine.dispose()


def test_downgrade_removes_table_and_upgrade_is_repeatable(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            project_id, user_id, run_id = uuid4().hex, uuid4().hex, uuid4().hex
            _seed(connection, project_id, user_id, run_id)
            _insert(connection, project_id=project_id, user_id=user_id, run_id=run_id)
    finally:
        engine.dispose()

    command.downgrade(config, "0056")
    engine = _engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert TABLE not in tables
        # 回滚 0057 不得连带删除 0056 的运行表。
        assert RUNS in tables
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        assert TABLE in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    schema = f"ddp_processing_steps_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            assert TABLE in inspector.get_table_names()
            assert "ix_processing_run_steps_project_run_id" in {
                index["name"] for index in inspector.get_indexes(TABLE)
            }
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
                        "(id, project_id, pipeline_version, input_refs, status, "
                        "created_by, updated_by) VALUES "
                        "(:id, :project_id, 'pipeline-v1', :refs, 'CREATED', "
                        ":user_id, :user_id)"
                    ),
                    {
                        "id": run_id,
                        "project_id": project_id,
                        "user_id": user_id,
                        "refs": '["raw/1"]',
                    },
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO processing_run_steps "
                            "(id, project_id, run_id, stage, attempt, status, started_at, "
                            "created_by, updated_by) VALUES "
                            "(:id, :project_id, :run_id, 'FILE_VALIDATION', 1, 'SKIPPED', "
                            "CURRENT_TIMESTAMP, :user_id, :user_id)"
                        ),
                        {
                            "id": uuid4(),
                            "project_id": project_id,
                            "run_id": run_id,
                            "user_id": user_id,
                        },
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "0056")
        engine = create_engine(url)
        try:
            assert TABLE not in inspect(engine).get_table_names()
        finally:
            engine.dispose()
        command.upgrade(config, "head")
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
