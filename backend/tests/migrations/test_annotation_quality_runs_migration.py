"""迁移 0035：质量检查执行记录两张表。

验证四件事：

1. 升级后两张表与索引齐全，且表结构由**显式 DDL** 建立（不依赖
   ``Model.__table__.create()``——本仓库刚因该写法在全新库上失败）；
2. 复合外键真的把跨项目数据挡住（不是"有约束名、无实际约束"）；
3. ``passed`` 允许 NULL（"没有结论"），CHECK 只约束枚举与计数；
4. downgrade 是严格逆操作，可往返。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
RUNS = "annotation_quality_runs"
FINDINGS = "annotation_quality_findings"
SHA = "sha256:" + "a" * 64
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")


def _config(db_path: Path) -> Config:
    return _config_for_url(f"sqlite+pysqlite:///{db_path}")


def _config_for_url(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：PG 的 ``?options=-csearch_path%3D...``
    # 里那个 ``%`` 必须转义成 ``%%``，否则 set_main_option 直接抛 ValueError。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _tables(db_path: Path) -> set[str]:
    engine = _engine(db_path)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _index_names(db_path: Path, table: str) -> set[str]:
    engine = _engine(db_path)
    try:
        return {item["name"] for item in inspect(engine).get_indexes(table)}
    finally:
        engine.dispose()


def test_migration_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "quality_runs.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    tables = _tables(db_path)
    assert RUNS in tables
    assert FINDINGS in tables
    assert "ix_annotation_quality_runs_project_submission_created" in _index_names(db_path, RUNS)
    assert "ix_annotation_quality_findings_project_run" in _index_names(db_path, FINDINGS)
    assert "ix_annotation_quality_findings_project_annotation" in _index_names(db_path, FINDINGS)

    command.downgrade(config, "0032")
    remaining = _tables(db_path)
    assert RUNS not in remaining
    assert FINDINGS not in remaining

    # 再次升级必须仍然成功：回滚不能留下半删除的索引/约束。
    command.upgrade(config, "head")
    tables = _tables(db_path)
    assert RUNS in tables and FINDINGS in tables


def test_passed_column_is_nullable_and_checks_enforce_enums(tmp_path: Path) -> None:
    db_path = tmp_path / "quality_nullable.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        columns = {item["name"]: item for item in inspect(engine).get_columns(FINDINGS)}
        assert columns["passed"]["nullable"] is True
        run_columns = {item["name"]: item for item in inspect(engine).get_columns(RUNS)}
        assert run_columns["rule_set_version"]["nullable"] is False
        assert run_columns["finished_at"]["nullable"] is False
    finally:
        engine.dispose()


def _seed_project_and_user(connection) -> tuple[str, str, str]:
    project_id, user_id, submission_id = uuid4().hex, uuid4().hex, uuid4().hex
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, '项目')"),
        {"id": project_id, "code": f"p-{project_id[:8]}"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, '标注员')"),
        {"id": user_id},
    )
    return project_id, user_id, submission_id


def test_status_check_rejects_unknown_value(tmp_path: Path) -> None:
    db_path = tmp_path / "quality_status.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            project_id, user_id, submission_id = _seed_project_and_user(connection)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_quality_runs (id, project_id, submission_id,"
                        " rule_set_version, status, error_count, warn_count, started_at,"
                        " finished_at, created_by) VALUES (:id, :pid, :sid, :rsv, 'RUNNING',"
                        " 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :uid)"
                    ),
                    {
                        "id": uuid4().hex,
                        "pid": project_id,
                        "sid": submission_id,
                        "rsv": SHA,
                        "uid": user_id,
                    },
                )
    finally:
        engine.dispose()


def test_rule_set_version_format_is_enforced(tmp_path: Path) -> None:
    """``rule_set_version`` 必须是 ``sha256:<64 hex>``；手写 v1 会被拒绝。"""
    db_path = tmp_path / "quality_version.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            project_id, user_id, submission_id = _seed_project_and_user(connection)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_quality_runs (id, project_id, submission_id,"
                        " rule_set_version, status, error_count, warn_count, started_at,"
                        " finished_at, created_by) VALUES (:id, :pid, :sid, 'v1', 'PASSED',"
                        " 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :uid)"
                    ),
                    {
                        "id": uuid4().hex,
                        "pid": project_id,
                        "sid": submission_id,
                        "uid": user_id,
                    },
                )
    finally:
        engine.dispose()


def test_counts_and_time_order_checks(tmp_path: Path) -> None:
    db_path = tmp_path / "quality_counts.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            project_id, user_id, submission_id = _seed_project_and_user(connection)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_quality_runs (id, project_id, submission_id,"
                        " rule_set_version, status, error_count, warn_count, started_at,"
                        " finished_at, created_by) VALUES (:id, :pid, :sid, :rsv, 'PASSED',"
                        " -1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :uid)"
                    ),
                    {
                        "id": uuid4().hex,
                        "pid": project_id,
                        "sid": submission_id,
                        "rsv": SHA,
                        "uid": user_id,
                    },
                )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_quality_runs (id, project_id, submission_id,"
                        " rule_set_version, status, error_count, warn_count, started_at,"
                        " finished_at, created_by) VALUES (:id, :pid, :sid, :rsv, 'PASSED',"
                        " 0, 0, '2026-11-16 12:00:00', '2026-11-16 11:00:00', :uid)"
                    ),
                    {
                        "id": uuid4().hex,
                        "pid": project_id,
                        "sid": submission_id,
                        "rsv": SHA,
                        "uid": user_id,
                    },
                )
    finally:
        engine.dispose()


def test_foreign_keys_are_declared_on_both_tables(tmp_path: Path) -> None:
    """外键名必须齐全：run→submission、finding→run、finding→annotation。"""
    db_path = tmp_path / "quality_fk.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        run_fks = {item["name"] for item in inspect(engine).get_foreign_keys(RUNS)}
        finding_fks = {item["name"] for item in inspect(engine).get_foreign_keys(FINDINGS)}
        assert "fk_annotation_quality_runs_project_submission" in run_fks
        assert "fk_annotation_quality_findings_project_run" in finding_fks
        assert "fk_annotation_quality_findings_project_annotation" in finding_fks
    finally:
        engine.dispose()


def test_findings_require_existing_run(tmp_path: Path) -> None:
    """子表不能在父表缺行时独立插入：否则执行记录会出现孤立明细。"""
    db_path = tmp_path / "quality_fk_enforced.db"
    command.upgrade(_config(db_path), "head")
    engine = _engine(db_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys = ON"))
            project_id, _user_id, _submission_id = _seed_project_and_user(connection)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_quality_findings (id, project_id, run_id,"
                        " annotation_id, rule_id, rule_version, severity, passed, message)"
                        " VALUES (:id, :pid, :rid, :aid, 'required_labels_present', 1,"
                        " 'ERROR', NULL, '缺少必填标签')"
                    ),
                    {
                        "id": uuid4().hex,
                        "pid": project_id,
                        "rid": uuid4().hex,
                        "aid": uuid4().hex,
                    },
                )
    finally:
        engine.dispose()


def test_no_model_table_create_used_in_migration() -> None:
    """守卫：0035 迁移不得调用 ``__table__.create()`` 或 ``metadata.create_all()``。

    这条测试是回归护栏——上一次用模型表建表导致全新库升级失败：它绕过
    metadata 级 ``after_create`` 事件，触发器不会被安装，"迁移过的库"与
    ``create_all`` 建出的库结构不一致。

    用 AST 只检查**调用点**而不是源码文本：迁移的说明性 docstring 里本来就会
    提到这两种写法（正是为了解释为什么不用），按文本断言会把文档本身判成违规。
    """
    migration = BACKEND_DIR / "migrations" / "versions" / "0035_annotation_quality_runs.py"
    tree = ast.parse(migration.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"create", "create_all"}:
            offenders.append(f"{func.attr} @ line {node.lineno}")
    assert offenders == [], f"迁移中出现了模型级建表调用：{offenders}"

    op_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    }
    assert "create_table" in op_calls
    assert "drop_table" in op_calls


_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    """真实 PostgreSQL 上的升级/回滚与 CHECK 约束。

    SQLite 的 CHECK 与 ``substr`` 语义足够接近，但它不能替代验收目标库：
    ``Uuid`` / ``Boolean`` 的落库形态、DDL 事务性与约束命名都只在 PG 上才真实。
    用独立 schema 隔离，跑完清理，避免污染共享测试库。
    """
    schema = f"ddp_quality_{uuid4().hex}"
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
            assert RUNS in inspector.get_table_names()
            assert FINDINGS in inspector.get_table_names()
            assert {
                "ck_annotation_quality_runs_status_allowed",
                "ck_annotation_quality_runs_rule_set_version_format",
                "ck_annotation_quality_runs_finished_after_started",
            } <= {item["name"] for item in inspector.get_check_constraints(RUNS)}

            with engine.begin() as connection:
                project_id, user_id, submission_id = _seed_project_and_user(connection)
                for params in (
                    {"status": "RUNNING", "rsv": SHA},
                    {"status": "PASSED", "rsv": "v1"},
                ):
                    # 必须包 savepoint：PostgreSQL 里一条语句违反 CHECK 后整个事务
                    # 进入 aborted 状态，后续语句一律 InFailedSqlTransaction。
                    with pytest.raises(IntegrityError), connection.begin_nested():
                        connection.execute(
                            text(
                                "INSERT INTO annotation_quality_runs (id, project_id,"
                                " submission_id, rule_set_version, status, error_count,"
                                " warn_count, started_at, finished_at, created_by)"
                                " VALUES (:id, :pid, :sid, :rsv, :status, 0, 0,"
                                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :uid)"
                            ),
                            {
                                "id": uuid4().hex,
                                "pid": project_id,
                                "sid": submission_id,
                                "uid": user_id,
                                **params,
                            },
                        )
        finally:
            engine.dispose()

        command.downgrade(config, "0032")
        engine = create_engine(url)
        try:
            remaining = set(inspect(engine).get_table_names())
            assert RUNS not in remaining and FINDINGS not in remaining
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
