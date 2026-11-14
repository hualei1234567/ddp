"""迁移测试：0024 数据源登记表可升级、可回滚，且关键约束真的落到库里。

重点验证两条"不能只靠应用层"的约束：
1. ``revision`` 必须是 40 位 commit sha（分支名进不了库）；
2. ``access_condition='GATED'`` 与 ``is_gated`` 必须一致。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
_SHA = "a" * 40


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def test_stage7_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "stage7.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"data_sources", "data_source_imports"} <= tables

    command.downgrade(config, "0023")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert not {"data_sources", "data_source_imports"} & tables


def _seed_project(conn) -> tuple[str, str]:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    pid, uid = str(uuid4()), str(uuid4())
    conn.execute(
        text(
            "insert into projects (id, code, name, created_at, updated_at) "
            "values (:id, :code, :name, :now, :now)"
        ),
        {"id": pid, "code": f"p-{pid[:8]}", "name": "测试项目", "now": now},
    )
    conn.execute(
        text(
            "insert into users (id, display_name, created_at, updated_at) "
            "values (:id, :name, :now, :now)"
        ),
        {"id": uid, "name": "测试用户", "now": now},
    )
    return pid, uid


def _insert_source(conn, pid: str, uid: str, **overrides: object) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    values: dict[str, object] = {
        "id": str(uuid4()),
        "pid": pid,
        "uid": uid,
        "code": f"hf-{uuid4().hex[:8]}",
        "name": "数据集",
        "repo_id": "lerobot/aloha_sim_insertion_human",
        "revision": _SHA,
        "license": "Apache-2.0",
        "access": "PUBLIC",
        "gated": 0,
        "now": now,
    }
    values.update(overrides)
    conn.execute(
        text(
            "insert into data_sources "
            "(id, project_id, code, name, kind, repo_id, revision, license, "
            " access_condition, is_gated, metadata_json, version, "
            " created_at, updated_at, created_by, updated_by) "
            "values (:id, :pid, :code, :name, 'HUGGINGFACE', :repo_id, :revision, "
            " :license, :access, :gated, '{}', 1, :now, :now, :uid, :uid)"
        ),
        values,
    )


def test_branch_name_revision_is_rejected_by_database(tmp_path: Path) -> None:
    """`main` 会移动；只存分支名的导入不可复现，数据库层直接拒绝。"""
    db_path = tmp_path / "rev.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            pid, uid = _seed_project(conn)
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_source(conn, pid, uid, revision="main")
    finally:
        engine.dispose()


def test_gated_condition_consistency_enforced_by_database(tmp_path: Path) -> None:
    db_path = tmp_path / "gated.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            pid, uid = _seed_project(conn)
        # GATED 但 is_gated=0：库里必须拒绝。
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_source(conn, pid, uid, access="GATED", gated=0)
        # PUBLIC 但 is_gated=1：同样拒绝。
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_source(conn, pid, uid, access="PUBLIC", gated=1)
        # 一致的组合应当通过。
        with engine.begin() as conn:
            _insert_source(conn, pid, uid, access="GATED", gated=1)
    finally:
        engine.dispose()
