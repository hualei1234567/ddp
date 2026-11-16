"""迁移测试：0041 给素材四表装状态兜底触发器。

覆盖三件事：

1. **触发器真的会拦**：非 ``DRAFT`` 初始状态被拒绝、非法迁移被拒绝。
   只断言"触发器存在"是不够的——SQLite 里触发器写进 DDL 但条件写错时
   照样"存在"却不起作用。
2. **合法路径不被误伤**：``DRAFT → ACTIVE → ARCHIVED`` 必须能走通，
   否则应用层每个激活请求都会 500。
3. **双向可迁移**：``downgrade`` 后触发器消失、数据仍在，再次 ``upgrade`` 成功。

全部走 Alembic ``op.create_table`` / 本迁移的 ``upgrade``，不使用
``Model.__table__.create``。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.session import create_db_engine
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
ASSET_TABLES = ("collection_plans", "collection_sops", "scenarios", "action_specs")


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _fk_engine(db_path: Path) -> Engine:
    """开启 ``PRAGMA foreign_keys=ON`` 的引擎，与应用同源。"""
    return create_db_engine(
        f"sqlite+pysqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )


def _trigger_names(db_path: Path, table: str) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"),
                {"t": table},
            ).fetchall()
        return {row[0] for row in rows}
    finally:
        engine.dispose()


def _seed(connection: object, project_id: str, user_id: str) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO projects (id, code, name) VALUES (?, ?, ?)",
        (project_id, f"p-{project_id[:6]}", "项目"),
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO users (id, display_name) VALUES (?, ?)",
        (user_id, "用户"),
    )


def _insert_plan(
    connection: object,
    *,
    plan_id: str,
    project_id: str,
    user_id: str,
    status: str,
) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        "INSERT INTO collection_plans (id, project_id, code, name, plan_version, status, "
        "version, created_by, updated_by) VALUES (?, ?, ?, ?, 1, ?, 1, ?, ?)",
        (plan_id, project_id, f"P-{plan_id[:6]}", "方案", status, user_id, user_id),
    )


def test_upgrade_installs_triggers_on_all_asset_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "guards.db"
    command.upgrade(_config(db_path), "head")

    for table in ASSET_TABLES:
        names = _trigger_names(db_path, table)
        assert f"trg_{table}_initial_status" in names
        assert f"trg_{table}_transition" in names


def test_non_draft_initial_status_is_rejected(tmp_path: Path) -> None:
    """初始状态必须 DRAFT；直接插入 ACTIVE 必须被数据库拒绝。

    这条守的是"绕过应用层建素材"：只要有人手工插行，状态机就从中间开始，
    审计也就无从解释第一次迁移发生在什么时候。
    """
    db_path = tmp_path / "initial.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id = uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
        with pytest.raises(IntegrityError, match="collection_asset_initial_status_invalid"):
            with engine.begin() as connection:
                _insert_plan(
                    connection,
                    plan_id=uuid4().hex,
                    project_id=project_id,
                    user_id=user_id,
                    status="ACTIVE",
                )
    finally:
        engine.dispose()


def test_illegal_transition_is_rejected_by_database(tmp_path: Path) -> None:
    """终态不可迁出：``ARCHIVED → DRAFT`` 必须被数据库拒绝。"""
    db_path = tmp_path / "transition.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    project_id, user_id, plan_id = uuid4().hex, uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
            _insert_plan(
                connection,
                plan_id=plan_id,
                project_id=project_id,
                user_id=user_id,
                status="DRAFT",
            )
            connection.execute(
                text("UPDATE collection_plans SET status='ACTIVE' WHERE id=:id"),
                {"id": plan_id},
            )
            connection.execute(
                text("UPDATE collection_plans SET status='ARCHIVED' WHERE id=:id"),
                {"id": plan_id},
            )

        with pytest.raises(IntegrityError, match="collection_asset_state_transition_invalid"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE collection_plans SET status='DRAFT' WHERE id=:id"),
                    {"id": plan_id},
                )
    finally:
        engine.dispose()


def test_legal_lifecycle_passes_and_downgrade_is_reversible(tmp_path: Path) -> None:
    """合法链路必须能走通；``downgrade`` 后触发器消失但数据保留。"""
    db_path = tmp_path / "reversible.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    engine = _fk_engine(db_path)
    project_id, user_id, plan_id = uuid4().hex, uuid4().hex, uuid4().hex
    try:
        with engine.begin() as connection:
            _seed(connection, project_id, user_id)
            _insert_plan(
                connection,
                plan_id=plan_id,
                project_id=project_id,
                user_id=user_id,
                status="DRAFT",
            )
            # 合法迁移：不触发任何守卫。
            connection.execute(
                text("UPDATE collection_plans SET status='ACTIVE' WHERE id=:id"),
                {"id": plan_id},
            )
            connection.execute(
                text("UPDATE collection_plans SET status='ARCHIVED' WHERE id=:id"),
                {"id": plan_id},
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0040")
    for table in ASSET_TABLES:
        names = _trigger_names(db_path, table)
        assert f"trg_{table}_initial_status" not in names
        assert f"trg_{table}_transition" not in names

    # 回滚不改数据：本迁移只加约束，没有回填。
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            status = connection.execute(
                text("SELECT status FROM collection_plans WHERE id=:id"),
                {"id": plan_id},
            ).scalar_one()
        assert status == "ARCHIVED"
        assert "collection_plans" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # 再次升级必须成功（证明可重复执行，不是一次性脚本）。
    command.upgrade(config, "head")
    for table in ASSET_TABLES:
        assert f"trg_{table}_transition" in _trigger_names(db_path, table)
