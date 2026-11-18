"""迁移测试：0056 建立 processing_runs。

覆盖建表、索引、状态/阶段 CHECK、FAILED 错误字段兜底与双向迁移。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "processing_runs"


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _engine(db_path: Path) -> Engine:
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _seed(connection: object, project_id: str, user_id: str) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (project_id, f"p-{project_id[:6]}", "项目"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user_id, "用户"),
    )


def _insert(
    connection: object,
    *,
    project_id: str,
    user_id: str,
    status: str = "CREATED",
    stage: str | None = None,
    attempt: int = 0,
    started_at: bool = False,
    finished_at: bool = False,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO processing_runs "
        "(id, project_id, pipeline_version, input_refs, status, stage, attempt, "
        "started_at, finished_at, error_code, error_summary, created_by, updated_by) "
        "VALUES (?, ?, 'pipeline-v1', '[\"raw/1\"]', ?, ?, ?, "
        "CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, "
        "CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, ?, ?, ?, ?)",
        (
            uuid4().hex,
            project_id,
            status,
            stage,
            attempt,
            int(started_at),
            int(finished_at),
            error_code,
            error_summary,
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
            "pipeline_version",
            "input_refs",
            "output_refs",
            "status",
            "stage",
            "attempt",
            "retry_count",
            "error_code",
            "error_summary",
            "started_at",
            "finished_at",
            "version",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        } <= columns
        indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
        assert "ix_processing_runs_project_status_created_at" in indexes
        checks = {check["name"] for check in inspector.get_check_constraints(TABLE)}
        assert {
            "ck_processing_runs_status_allowed",
            "ck_processing_runs_stage_allowed",
            "ck_processing_runs_failed_requires_error",
            "ck_processing_runs_active_requires_stage",
        } <= checks
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("status", "stage", "attempt", "started_at", "finished_at", "match"),
    [
        ("BOGUS", None, 0, False, False, "status_allowed"),
        ("CREATED", "BOGUS", 0, False, False, "stage_allowed"),
        ("RUNNING", None, 1, True, False, "active_requires_stage"),
        ("FAILED", "FILE_VALIDATION", 1, True, True, "failed_requires_error"),
        ("SUCCEEDED", "FILE_VALIDATION", 1, True, False, "finished_requires_finished_at"),
    ],
)
def test_database_checks_reject_invalid_rows(
    tmp_path: Path,
    status: str,
    stage: str | None,
    attempt: int,
    started_at: bool,
    finished_at: bool,
    match: str,
) -> None:
    db_path = tmp_path / "checks.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
        with pytest.raises(IntegrityError, match=match):
            with engine.begin() as connection:
                _insert(
                    connection,
                    project_id=project_id,
                    user_id=user_id,
                    status=status,
                    stage=stage,
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
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
            project_id, user_id = uuid4().hex, uuid4().hex
            _seed(connection, project_id, user_id)
            _insert(connection, project_id=project_id, user_id=user_id)
    finally:
        engine.dispose()

    command.downgrade(config, "0054")
    engine = _engine(db_path)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        assert TABLE in inspect(engine).get_table_names()
    finally:
        engine.dispose()
