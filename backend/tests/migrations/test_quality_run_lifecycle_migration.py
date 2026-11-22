"""迁移测试：0070 给质量运行拆出生命周期。

覆盖：升级后新增列与回填、未完成运行的约束、回滚保护（存在未完成运行时报错）、
清理后可回滚、以及配置真实 PostgreSQL 时的同一往返。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "quality_runs"
RULE_SET = "sha256:" + "a" * 64
PROJECT = UUID("00000000-0000-7000-8000-000000000601")
ACTOR = UUID("00000000-0000-7000-8000-000000000602")
SUBJECT = UUID("00000000-0000-7000-8000-000000000603")
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")

_LEGACY_INSERT = text(
    f"INSERT INTO {TABLE} "  # noqa: S608 - 表名是模块常量
    "(id, project_id, subject_type, subject_id, rule_set_version, status, score, "
    "finding_count, error_count, warn_count, category_summary, started_at, "
    "finished_at, created_at, created_by) "
    "VALUES (:id, :project, 'EPISODE', :subject, :rules, 'PASSED', 1.0, 0, 0, 0, "
    "'{\"OVERALL\": {}}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :actor)"
)

_UNFINISHED_INSERT = text(
    f"INSERT INTO {TABLE} "  # noqa: S608 - 表名是模块常量
    "(id, project_id, subject_type, subject_id, rule_set_version, status, score, "
    "finding_count, error_count, warn_count, category_summary, started_at, "
    "finished_at, created_at, created_by, lifecycle, attempt, max_attempts) "
    "VALUES (:id, :project, 'EPISODE', :subject, :rules, :status, NULL, 0, 0, 0, "
    "'{}', :started, :finished, CURRENT_TIMESTAMP, :actor, :lifecycle, 1, :max_attempts)"
)


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


def _columns(engine: Engine) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspect(engine).get_columns(TABLE)}


def _legacy_params() -> dict[str, object]:
    return {
        "id": uuid4().hex,
        "project": PROJECT.hex,
        "subject": SUBJECT.hex,
        "rules": RULE_SET,
        "actor": ACTOR.hex,
    }


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "lifecycle.db"
    config = _config(db_path)
    command.upgrade(config, "0070")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_adds_lifecycle_columns_and_relaxes_times(engine: Engine) -> None:
    columns = _columns(engine)

    assert {"lifecycle", "attempt", "max_attempts", "error_code", "error_summary"} <= set(columns)
    assert columns["status"]["nullable"] is True
    assert columns["started_at"]["nullable"] is True
    assert columns["finished_at"]["nullable"] is True


def test_legacy_rows_are_backfilled_as_succeeded(tmp_path: Path) -> None:
    db_path = tmp_path / "backfill.db"
    config = _config(db_path)
    command.upgrade(config, "0069")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(_LEGACY_INSERT, _legacy_params())

    command.upgrade(config, "0070")
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT lifecycle, attempt, max_attempts FROM {TABLE}")  # noqa: S608
        ).one()
    engine.dispose()

    assert row.lifecycle == "SUCCEEDED"
    assert (row.attempt, row.max_attempts) == (1, 1)


def test_queued_run_must_not_carry_conclusion(engine: Engine) -> None:
    """数据库兜底：没跑完的运行写不进结论。"""
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                _UNFINISHED_INSERT,
                {
                    **_legacy_params(),
                    "lifecycle": "QUEUED",
                    # QUEUED 不得有 status；这里故意给值，期望被 CHECK 拒绝。
                    "status": "PASSED",
                    "started": None,
                    "finished": None,
                    "max_attempts": 1,
                },
            )


def test_failed_run_requires_error_code(engine: Engine) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                _UNFINISHED_INSERT,
                {
                    **_legacy_params(),
                    "lifecycle": "FAILED",
                    "status": None,
                    "started": "2026-11-22 10:00:00",
                    "finished": "2026-11-22 10:05:00",
                    "max_attempts": 1,
                },
            )


def test_cancelled_after_retry_keeps_last_error(engine: Engine) -> None:
    """重投后取消：上一次失败原因属于历史，不能逼着写入方伪造清空。"""
    with engine.begin() as conn:
        conn.execute(
            _UNFINISHED_INSERT,
            {
                **_legacy_params(),
                "lifecycle": "CANCELLED",
                "status": None,
                "started": "2026-11-22 10:00:00",
                "finished": "2026-11-22 10:05:00",
                "max_attempts": 3,
            },
        )
        conn.execute(
            text(
                f"UPDATE {TABLE} SET error_code = 'boom', "  # noqa: S608 - 表名是模块常量
                "error_summary = '规则实现异常' WHERE lifecycle = 'CANCELLED'"
            )
        )
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT lifecycle, error_code FROM {TABLE}")  # noqa: S608
        ).one()
    assert row.lifecycle == "CANCELLED"
    assert row.error_code == "boom"


def test_downgrade_refuses_while_unfinished_runs_exist(engine: Engine, tmp_path: Path) -> None:
    with engine.begin() as conn:
        conn.execute(
            _UNFINISHED_INSERT,
            {
                **_legacy_params(),
                "lifecycle": "RUNNING",
                "status": None,
                "started": "2026-11-22 10:00:00",
                "finished": None,
                "max_attempts": 1,
            },
        )

    config = _config(tmp_path / "lifecycle.db")
    with pytest.raises(RuntimeError, match="无法无损回滚"):
        command.downgrade(config, "0069")


def test_downgrade_restores_old_shape_after_cleanup(engine: Engine, tmp_path: Path) -> None:
    config = _config(tmp_path / "lifecycle.db")
    command.downgrade(config, "0069")

    columns = _columns(engine)
    assert "lifecycle" not in columns
    assert columns["status"]["nullable"] is False
    assert columns["started_at"]["nullable"] is False
    assert columns["finished_at"]["nullable"] is False


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="需要 DDP_TEST_POSTGRES_URL 指向真实 PostgreSQL",
)
def test_postgres_roundtrip(tmp_path: Path) -> None:
    config = _config_for_url(_POSTGRES_URL)
    command.upgrade(config, "0070")
    columns = {c["name"]: c for c in inspect(create_engine(_POSTGRES_URL)).get_columns(TABLE)}
    assert columns["started_at"]["nullable"] is True
    command.downgrade(config, "0069")
