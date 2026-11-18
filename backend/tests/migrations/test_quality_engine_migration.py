"""迁移测试：0058 建立 quality_runs 与 quality_findings。

覆盖建表、索引、CHECK（类别/状态/分数/规则集版本）、复合外键、唯一键、
双向迁移；配置 PostgreSQL 时再做一次真实库往返。
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
RUNS = "quality_runs"
FINDINGS = "quality_findings"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_START = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
_FINISH = datetime(2026, 11, 18, 12, 0, 5, tzinfo=UTC)
_SHA = "sha256:" + "a" * 64
_SUMMARY = "{}"


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


def _seed(connection: object, project_id: str, user_id: str) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (project_id, f"p-{project_id[:6]}", "项目"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user_id, "用户"),
    )


def _insert_run(
    connection: object,
    *,
    project_id: str,
    user_id: str,
    run_id: str | None = None,
    subject_type: str = "EPISODE",
    rule_set_version: str = _SHA,
    status: str = "PASSED",
    score: float | None = 1.0,
) -> str:
    run_id = run_id or uuid4().hex
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO quality_runs "
        "(id, project_id, subject_type, subject_id, rule_set_version, status, score, "
        "finding_count, error_count, warn_count, category_summary, started_at, "
        "finished_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, ?, ?)",
        (
            run_id,
            project_id,
            subject_type,
            uuid4().hex,
            rule_set_version,
            status,
            score,
            _SUMMARY,
            _START,
            _FINISH,
            user_id,
        ),
    )
    return run_id


def _insert_finding(
    connection: object,
    *,
    project_id: str,
    run_id: str,
    category: str = "INTEGRITY",
    rule_id: str = "assets_present",
    status: str = "PASS",
    score: float | None = 1.0,
    subject_ref: str = "",
) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO quality_findings "
        "(id, project_id, run_id, category, rule_id, rule_version, severity, status, "
        "subject_ref, message, evidence, score, created_at) "
        "VALUES (?, ?, ?, ?, ?, '1.1.0', 'ERROR', ?, ?, '判定说明', NULL, ?, ?)",
        (
            uuid4().hex,
            project_id,
            run_id,
            category,
            rule_id,
            status,
            subject_ref,
            score,
            _START,
        ),
    )


def test_upgrade_creates_tables_columns_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.db"
    command.upgrade(_config(db_path), "head")

    engine = _engine(db_path)
    try:
        inspector = inspect(engine)
        assert {RUNS, FINDINGS} <= set(inspector.get_table_names())
        run_columns = {column["name"] for column in inspector.get_columns(RUNS)}
        assert {
            "id",
            "project_id",
            "subject_type",
            "subject_id",
            "rule_set_version",
            "status",
            "score",
            "finding_count",
            "error_count",
            "warn_count",
            "category_summary",
            "started_at",
            "finished_at",
            "created_at",
            "created_by",
        } <= run_columns
        finding_columns = {column["name"] for column in inspector.get_columns(FINDINGS)}
        assert {
            "id",
            "project_id",
            "run_id",
            "category",
            "rule_id",
            "rule_version",
            "severity",
            "status",
            "subject_ref",
            "message",
            "evidence",
            "score",
            "created_at",
        } <= finding_columns
        run_indexes = {index["name"] for index in inspector.get_indexes(RUNS)}
        assert "ix_quality_runs_project_subject_created" in run_indexes
        finding_indexes = {index["name"] for index in inspector.get_indexes(FINDINGS)}
        assert "ix_quality_findings_project_run_category" in finding_indexes
        assert "ix_quality_findings_project_run_status" in finding_indexes
        finding_uniques = {
            constraint["name"] for constraint in inspector.get_unique_constraints(FINDINGS)
        }
        assert "uq_quality_findings_run_subject_rule" in finding_uniques
        finding_fks = {constraint["name"] for constraint in inspector.get_foreign_keys(FINDINGS)}
        assert "fk_quality_findings_project_run" in finding_fks
    finally:
        engine.dispose()


def test_check_constraints_and_downgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "schema.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        project_id, user_id = uuid4().hex, uuid4().hex
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
            run_id = _insert_run(connection, project_id=project_id, user_id=user_id)
            _insert_finding(connection, project_id=project_id, run_id=run_id)

        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=run_id,
                category="BOGUS",
                rule_id="r-bogus",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=run_id,
                category="OVERALL",
                rule_id="r-overall",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=run_id,
                status="NOT_APPLICABLE",
                score=1.0,
                rule_id="r-na-score",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=run_id,
                status="PASS",
                score=None,
                rule_id="r-pass-null",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=str(uuid4()),
                rule_id="r-missing-run",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_run(
                connection,
                project_id=project_id,
                user_id=user_id,
                rule_set_version="not-a-sha",
            )
        with (
            engine.connect() as connection,
            pytest.raises(IntegrityError),
            connection.begin_nested(),
        ):
            _insert_finding(
                connection,
                project_id=project_id,
                run_id=run_id,
                rule_id="assets_present",
            )
    finally:
        engine.dispose()

    command.downgrade(_config(db_path), "0057")
    engine = _engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert RUNS not in tables
        assert FINDINGS not in tables
    finally:
        engine.dispose()

    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        assert {RUNS, FINDINGS} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    schema = f"ddp_quality_engine_{uuid4().hex}"
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
            assert {RUNS, FINDINGS} <= set(inspector.get_table_names())
            project_id, user_id = uuid4(), uuid4()
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
                        "INSERT INTO quality_runs "
                        "(id, project_id, subject_type, subject_id, rule_set_version, "
                        "status, score, finding_count, error_count, warn_count, "
                        "category_summary, started_at, finished_at, created_by) VALUES "
                        "(:id, :project_id, 'EPISODE', :subject_id, :sha, 'PASSED', 1.0, "
                        "1, 0, 0, :summary, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user_id)"
                    ),
                    {
                        "id": uuid4(),
                        "project_id": project_id,
                        "subject_id": uuid4(),
                        "sha": _SHA,
                        "summary": _SUMMARY,
                        "user_id": user_id,
                    },
                )
        finally:
            engine.dispose()

        command.downgrade(config, "0057")
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
            assert RUNS not in tables
            assert FINDINGS not in tables
        finally:
            engine.dispose()
        command.upgrade(config, "head")
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
