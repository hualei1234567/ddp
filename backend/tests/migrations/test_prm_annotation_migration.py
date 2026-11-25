"""迁移测试：0078 新建 ``prm_annotations``（V52-020）。

覆盖 SQLite 的 upgrade → downgrade → upgrade 往返，以及两条不能靠
应用层兜底的数据库约束：

- ``project_id + trajectory_id`` 唯一：同一轨迹在项目内只有一份步级标注；
- ``marked_count <= step_count``：冗余计数列的一致性由数据库兜底，
  应用层 bug 不该把"标了 5 步但轨迹只有 3 步"写进库。

本迁移只建表（无 PG 专有语句、无 ``DROP CONSTRAINT``），因此与既有约定
一致跑 SQLite；真实 PostgreSQL 往返见 ``test_capture_sessions_migration.py``
的可选 PG 用例模式（未配置 ``DDP_TEST_POSTGRES_URL`` 时跳过）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "prm_annotations"


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _seed_project(engine: Engine) -> tuple[str, str]:
    project_id, user_id = uuid4().hex, uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
            {"id": project_id, "code": f"p-{project_id[:8]}", "name": "PRM 项目"},
        )
        connection.execute(
            text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
            {"id": user_id, "name": "标注员"},
        )
    return project_id, user_id


def test_sqlite_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "prm_annotations.db"
    config = _config(db_path)

    command.upgrade(config, "0074")
    engine = _engine(db_path)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns(TABLE)}
        assert {
            "id",
            "project_id",
            "trajectory_id",
            "task_goal",
            "step_actions",
            "marks",
            "fork_lineage",
            "step_count",
            "marked_count",
            "version",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        } <= columns

        project_id, user_id = _seed_project(engine)
        # 合法行：3 步轨迹标了 1 步（部分标注）。
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO prm_annotations "
                    "(id, project_id, trajectory_id, task_goal, step_actions, marks, "
                    "fork_lineage, step_count, marked_count, version, "
                    "created_by, updated_by) VALUES ("
                    ":id, :project, 'traj-1', '移动杯子', :actions, :marks, NULL, "
                    "3, 1, 1, :user, :user)"
                ),
                {
                    "id": uuid4().hex,
                    "project": project_id,
                    "actions": '["查表", "抓取", "放置"]',
                    "marks": '[{"step_index": 0, "label": "CORRECT"}]',
                    "user": user_id,
                },
            )

        # 同一轨迹重复标注：项目内业务键唯一，拒绝。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO prm_annotations "
                        "(id, project_id, trajectory_id, task_goal, step_actions, marks, "
                        "fork_lineage, step_count, marked_count, version, "
                        "created_by, updated_by) VALUES ("
                        ":id, :project, 'traj-1', NULL, :actions, '[]', NULL, "
                        "3, 0, 1, :user, :user)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project": project_id,
                        "actions": '["查表", "抓取", "放置"]',
                        "user": user_id,
                    },
                )

        # marked_count 超过 step_count：CHECK 拒绝。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO prm_annotations "
                        "(id, project_id, trajectory_id, task_goal, step_actions, marks, "
                        "fork_lineage, step_count, marked_count, version, "
                        "created_by, updated_by) VALUES ("
                        ":id, :project, 'traj-2', NULL, :actions, '[]', NULL, "
                        "2, 3, 1, :user, :user)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project": project_id,
                        "actions": '["a", "b"]',
                        "user": user_id,
                    },
                )

        # 零步轨迹：CHECK 拒绝（轨迹至少 1 步）。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO prm_annotations "
                        "(id, project_id, trajectory_id, task_goal, step_actions, marks, "
                        "fork_lineage, step_count, marked_count, version, "
                        "created_by, updated_by) VALUES ("
                        ":id, :project, 'traj-3', NULL, '[]', '[]', NULL, "
                        "0, 0, 1, :user, :user)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project": project_id,
                        "user": user_id,
                    },
                )
    finally:
        engine.dispose()

    command.downgrade(config, "0074")
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
