"""迁移测试：0073 为 quality_findings 增加证据定位列（V51-010）。

覆盖：

- ``upgrade`` 后四列存在，且 ``evidence_time_range_consistent`` 生效
  （起止必须同时存在、起点必须早于终点）；
- ``downgrade`` 删除四列与约束，不触碰其它列；
- 再次 ``upgrade`` 幂等；
- 配置真实 PostgreSQL 时在同一 schema 内做同一往返。

## 为什么用 BIGINT 而不是 NUMERIC/DOUBLE

纳秒时间戳约 ``1.8e18``，超过 IEEE-754 双精度可精确表示范围（``2^53``）。
本测试显式写入一个远大于 ``2^53`` 的纳秒值并逐位读回，确认列类型没有静默
改值——这是"能定位到正确时刻"的数据层前提。
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
TABLE = "quality_findings"
COLUMNS = (
    "evidence_start_time_ns",
    "evidence_end_time_ns",
    "evidence_stream_id",
    "evidence_field_path",
)
_TIME_RANGE_CHECK = "ck_quality_findings_evidence_time_range_consistent"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")

#: 纳秒时间戳（2025-11-24 左右），远大于 2^53 = 9_007_199_254_740_992。
_NS = 1_763_510_400_000_000_000
_SHA = "sha256:" + "a" * 64


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
    # ConfigParser 会对 ``%`` 插值：PG 的 ``?options=-csearch_path%3D...`` 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(url: str) -> Engine:
    return create_engine(url)


def _columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(TABLE)}


def _check_names(engine: Engine) -> set[str]:
    return {str(item.get("name") or "") for item in inspect(engine).get_check_constraints(TABLE)}


def _seed(connection, project_id: str, user_id: str) -> None:
    """用 SQLAlchemy ``text()`` 绑定：``?`` 占位符只在 SQLite 方言下成立。"""
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
        {"id": project_id, "code": f"p-{project_id[:6]}", "name": "项目"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
        {"id": user_id, "name": "用户"},
    )


_RUN_SQL = (
    "INSERT INTO quality_runs "
    "(id, project_id, subject_type, subject_id, rule_set_version, status, score, "
    "finding_count, error_count, warn_count, category_summary, started_at, "
    "finished_at, created_by) "
    "VALUES (:id, :project_id, 'EPISODE', :subject_id, :sha, 'PASSED', 1.0, "
    "1, 0, 0, :summary, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :user)"
)

_FINDING_SQL = (
    "INSERT INTO quality_findings "
    "(id, project_id, run_id, category, rule_id, rule_version, severity, status, "
    "subject_ref, message, score, evidence_start_time_ns, evidence_end_time_ns, "
    "evidence_stream_id, evidence_field_path) "
    "VALUES (:id, :project_id, :run_id, 'TEMPORAL', 'stream_sync_aligned', '1.1.0', "
    "'ERROR', 'FAIL', '', '同步超差', 0.0, :start_ns, :end_ns, :stream, :field_path)"
)


def _run_params(project_id: str, user_id: str) -> dict[str, str]:
    return {
        "id": uuid4().hex,
        "project_id": project_id,
        "subject_id": uuid4().hex,
        "sha": _SHA,
        "summary": "{}",
        "user": user_id,
    }


def _finding_params(run: dict[str, str], **overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "id": uuid4().hex,
        "project_id": run["project_id"],
        "run_id": run["id"],
        "start_ns": _NS,
        "end_ns": _NS + 40_000_000,
        "stream": "wrist",
        "field_path": "streams",
    }
    params.update(overrides)
    return params


def _exercise_sqlite(engine: Engine) -> None:
    project_id, user_id = uuid4().hex, uuid4().hex
    with engine.connect() as connection:
        try:
            _seed(connection, project_id, user_id)
            run = _run_params(project_id, user_id)
            connection.execute(text(_RUN_SQL), run)

            # 1) 正常写入并把纳秒逐位读回（验证 BIGINT 没有精度损失）。
            params = _finding_params(run)
            connection.execute(text(_FINDING_SQL), params)
            stored = connection.execute(
                text(
                    "SELECT evidence_start_time_ns, evidence_end_time_ns, "
                    "evidence_stream_id, evidence_field_path FROM quality_findings "
                    "WHERE id = :id"
                ),
                {"id": params["id"]},
            ).one()
            assert int(stored[0]) == _NS
            assert int(stored[1]) == _NS + 40_000_000
            assert stored[0] > 2**53  # 确认确实超出浮点精确范围
            assert stored[2] == "wrist"
            assert stored[3] == "streams"

            # 2) 只有一端：CHECK 必须拒绝（半个区间是无意义数据）。
            with pytest.raises(IntegrityError):
                connection.execute(text(_FINDING_SQL), _finding_params(run, end_ns=None))
            # 3) 起点不早于终点：拒绝。
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(_FINDING_SQL),
                    _finding_params(run, start_ns=_NS + 1, end_ns=_NS),
                )
        finally:
            connection.rollback()


def test_evidence_location_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "qc_evidence.db"
    config = _config(db_path)

    command.upgrade(config, "0073")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = _columns(engine)
        for column in COLUMNS:
            assert column in columns
        assert _TIME_RANGE_CHECK in _check_names(engine)
        _exercise_sqlite(engine)
    finally:
        engine.dispose()

    # 回滚：四列与约束消失；不触碰既有列。
    command.downgrade(config, "0072")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = _columns(engine)
        for column in COLUMNS:
            assert column not in columns
        # 既有列保留（证明 downgrade 只删本迁移新增的东西）。
        for existing in ("id", "project_id", "run_id", "evidence", "score", "message"):
            assert existing in columns
        assert _TIME_RANGE_CHECK not in _check_names(engine)
    finally:
        engine.dispose()

    # 再次升级：列与约束恢复（幂等/可重放）。
    command.upgrade(config, "head")
    engine = _engine(f"sqlite+pysqlite:///{db_path}")
    try:
        columns = _columns(engine)
        for column in COLUMNS:
            assert column in columns
        assert _TIME_RANGE_CHECK in _check_names(engine)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_evidence_location_roundtrip_on_postgresql() -> None:
    schema = f"ddp_qc_evidence_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0073")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for column in COLUMNS:
                assert column in columns
            assert _TIME_RANGE_CHECK in _check_names(engine)
            # PG 的 BIGINT 同样必须逐位保留纳秒。
            project_id, user_id = uuid4().hex, uuid4().hex
            with engine.connect() as connection:
                try:
                    _seed(connection, project_id, user_id)
                    run = _run_params(project_id, user_id)
                    connection.execute(text(_RUN_SQL), run)
                    params = _finding_params(run)
                    connection.execute(text(_FINDING_SQL), params)
                    stored = connection.execute(
                        text(
                            "SELECT evidence_start_time_ns FROM quality_findings " "WHERE id = :id"
                        ),
                        {"id": params["id"]},
                    ).scalar_one()
                    assert int(stored) == _NS
                    with pytest.raises(IntegrityError):
                        connection.execute(text(_FINDING_SQL), _finding_params(run, end_ns=None))
                finally:
                    connection.rollback()
        finally:
            engine.dispose()

        command.downgrade(config, "0072")
        engine = create_engine(url)
        try:
            columns = _columns(engine)
            for column in COLUMNS:
                assert column not in columns
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
