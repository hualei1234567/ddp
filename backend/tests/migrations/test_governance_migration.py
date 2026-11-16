"""迁移测试：0043 建立 ``resource_quotas`` 与 ``usage_snapshots``。

覆盖：

1. 迁移只使用显式 DDL，不引用活的 ORM 模型；
2. 全新库 upgrade 到 head 后两表结构与约束齐全；
3. CHECK 真的会拒绝非法资源类型与负值（不是"写在 DDL 里但没生效"）；
4. 同一项目同类型唯一、不同项目可各配一条；
5. 删除项目级联删除配额与快照；
6. downgrade 后两表消失，再次 upgrade 成功；
7. 配置 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上验证一次。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATION_FILE = (
    BACKEND_DIR / "migrations" / "versions" / "0043_resource_quotas_and_usage_snapshots.py"
)
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


def _raw_engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _fk_engine(db_path: Path) -> Engine:
    """与应用同源、开启外键的引擎，用于证明级联删除真实生效。"""
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
        (project_id, f"p-{project_id[:6]}", "项目一"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user_id, "用户一"),
    )


def _insert_quota(
    connection: object,
    *,
    project_id: str,
    user_id: str,
    kind: str = "EPISODES",
    limit_value: int = 10,
) -> str:
    quota_id = uuid4().hex
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO resource_quotas (id, project_id, resource_kind, limit_value, "
            "version, created_by, updated_by) VALUES (:id, :project, :kind, :limit, "
            "1, :user, :user)"
        ),
        {
            "id": quota_id,
            "project": project_id,
            "kind": kind,
            "limit": limit_value,
            "user": user_id,
        },
    )
    return quota_id


def _insert_snapshot(
    connection: object,
    *,
    project_id: str,
    kind: str = "EPISODES",
    used_value: int = 3,
) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO usage_snapshots (project_id, resource_kind, used_value, "
            "measurement_source, measured_at) VALUES (:project, :kind, :used, "
            "'episodes.count', CURRENT_TIMESTAMP)"
        ),
        {"project": project_id, "kind": kind, "used": used_value},
    )


# --- 静态守卫 --------------------------------------------------------------
def test_migration_uses_explicit_ddl_not_live_models() -> None:
    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"create_table", "drop_table"} <= calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "create_all",
                "create",
            }, "迁移不得用活 ORM 模型或 metadata 建表"


# --- SQLite ---------------------------------------------------------------
def test_fresh_upgrade_creates_both_tables_with_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    command.upgrade(_config(db_path), "head")

    engine = _raw_engine(db_path)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"resource_quotas", "usage_snapshots"} <= tables

        quota_columns = {item["name"]: item for item in inspector.get_columns("resource_quotas")}
        assert quota_columns["limit_value"]["nullable"] is False
        assert quota_columns["version"]["nullable"] is False
        assert quota_columns["created_by"]["nullable"] is False
        assert quota_columns["updated_by"]["nullable"] is False

        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("resource_quotas")
        }
        assert ("project_id", "resource_kind") in uniques

        quota_checks = {item["name"] for item in inspector.get_check_constraints("resource_quotas")}
        assert {
            "ck_resource_quotas_resource_kind_allowed",
            "ck_resource_quotas_limit_non_negative",
            "ck_resource_quotas_version_positive",
        } <= quota_checks

        snapshot_checks = {
            item["name"] for item in inspector.get_check_constraints("usage_snapshots")
        }
        assert {
            "ck_usage_snapshots_resource_kind_allowed",
            "ck_usage_snapshots_used_non_negative",
        } <= snapshot_checks

        snapshot_pk = inspector.get_pk_constraint("usage_snapshots")
        assert snapshot_pk["constrained_columns"] == ["project_id", "resource_kind"]

        fk_targets = {
            item["referred_table"] for item in inspector.get_foreign_keys("resource_quotas")
        }
        assert {"projects", "users"} <= fk_targets
    finally:
        engine.dispose()


def test_invalid_kind_and_negative_values_are_rejected_by_database(tmp_path: Path) -> None:
    db_path = tmp_path / "checks.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)

        with pytest.raises(IntegrityError, match="CHECK"):
            with engine.begin() as connection:
                _insert_quota(
                    connection, project_id=project_id, user_id=user_id, kind="CPU_SECONDS"
                )

        with pytest.raises(IntegrityError, match="CHECK"):
            with engine.begin() as connection:
                _insert_quota(connection, project_id=project_id, user_id=user_id, limit_value=-1)

        with pytest.raises(IntegrityError, match="CHECK"):
            with engine.begin() as connection:
                _insert_snapshot(connection, project_id=project_id, kind="CPU_SECONDS")

        with pytest.raises(IntegrityError, match="CHECK"):
            with engine.begin() as connection:
                _insert_snapshot(connection, project_id=project_id, used_value=-1)
    finally:
        engine.dispose()


def test_quota_unique_is_scoped_to_project(tmp_path: Path) -> None:
    """同项目同类型唯一；不同项目各自一条——这是项目隔离的落点。"""
    db_path = tmp_path / "unique.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_a, project_b = uuid4().hex, uuid4().hex
    user_a, user_b = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_a, user_a)
            _seed(connection, project_b, user_b)
            _insert_quota(connection, project_id=project_a, user_id=user_a)
            _insert_quota(connection, project_id=project_b, user_id=user_b)

        with pytest.raises(IntegrityError, match="UNIQUE"):
            with engine.begin() as connection:
                _insert_quota(connection, project_id=project_a, user_id=user_a)
    finally:
        engine.dispose()


def test_deleting_project_cascades_quota_and_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "cascade.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
            _insert_quota(connection, project_id=project_id, user_id=user_id)
            _insert_snapshot(connection, project_id=project_id)

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})

        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM resource_quotas")).scalar() == 0
            assert connection.execute(text("SELECT COUNT(*) FROM usage_snapshots")).scalar() == 0
    finally:
        engine.dispose()


def test_downgrade_removes_tables_and_reupgrade_succeeds(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    command.downgrade(config, "0042")
    engine = _raw_engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "resource_quotas" not in tables
        assert "usage_snapshots" not in tables
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _raw_engine(db_path)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"resource_quotas", "usage_snapshots"} <= tables
    finally:
        engine.dispose()


# --- PostgreSQL（可选） ----------------------------------------------------
@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    """真实 PostgreSQL：升级、约束、级联与回滚。

    用独立 schema 隔离，跑完清理，避免污染共享测试库。
    """
    schema = f"ddp_governance_{uuid4().hex}"
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
            assert {"resource_quotas", "usage_snapshots"} <= set(inspector.get_table_names())

            project_id, user_id = uuid4(), uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, 'pg-a', '项目一')"),
                    {"id": project_id},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id, '用户')"),
                    {"id": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO resource_quotas (id, project_id, resource_kind, "
                        "limit_value, version, created_by, updated_by) VALUES "
                        "(:id, :project, 'EPISODES', 5, 1, :user, :user)"
                    ),
                    {"id": uuid4(), "project": project_id, "user": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO usage_snapshots (project_id, resource_kind, used_value, "
                        "measurement_source, measured_at) VALUES "
                        "(:project, 'EPISODES', 2, 'episodes.count', CURRENT_TIMESTAMP)"
                    ),
                    {"project": project_id},
                )

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO resource_quotas (id, project_id, resource_kind, "
                            "limit_value, version, created_by, updated_by) VALUES "
                            "(:id, :project, 'CPU_SECONDS', 1, 1, :user, :user)"
                        ),
                        {"id": uuid4(), "project": project_id, "user": user_id},
                    )

            command.downgrade(config, "0042")
            assert "resource_quotas" not in set(inspect(engine).get_table_names())
            command.upgrade(config, "head")
            assert "resource_quotas" in set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
