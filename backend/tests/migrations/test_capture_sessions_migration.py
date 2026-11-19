"""迁移测试：0066 新建 ``capture_sessions``。

覆盖 SQLite 与真实 PostgreSQL 的 upgrade → downgrade → upgrade 往返，
以及三个不能靠人工验收的数据库兜底：

- 初始状态必须是 ``SCHEDULED``；
- 状态迁移必须落在领域白名单内；
- 会话是执行记录，禁止删除。

配置 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍同一套往返。
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
TABLE = "capture_sessions"
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
_TRIGGERS = {
    "trg_capture_sessions_initial_status",
    "trg_capture_sessions_transition",
    "trg_capture_sessions_no_delete",
}


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
    # ConfigParser 的 % 插值：PG 的 ?options=...%3D... 必须转义。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite+pysqlite:///{db_path}")


def _columns(engine: Engine) -> dict[str, dict[str, object]]:
    return {column["name"]: column for column in inspect(engine).get_columns(TABLE)}


def _sqlite_triggers(engine: Engine) -> set[str]:
    with engine.begin() as connection:
        return {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master " "WHERE type = 'trigger' AND tbl_name = :t"),
                {"t": TABLE},
            )
        }


def _seed_sqlite(engine: Engine) -> tuple[str, str, str, str]:
    project_id, user_id, task_id, session_id = (uuid4().hex for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
            {"id": project_id, "code": f"p-{project_id[:8]}", "name": "项目"},
        )
        connection.execute(
            text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
            {"id": user_id, "name": "操作员"},
        )
        connection.execute(
            text(
                "INSERT INTO collection_tasks "
                "(id, project_id, code, name, status, priority, created_by, updated_by) "
                "VALUES (:id, :project, 'TASK-1', '任务', 'DRAFT', 'P1', :user, :user)"
            ),
            {"id": task_id, "project": project_id, "user": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO capture_sessions "
                "(id, project_id, code, task_id, status, created_by, updated_by) "
                "VALUES (:id, :project, 'S-1', :task, 'SCHEDULED', :user, :user)"
            ),
            {
                "id": session_id,
                "project": project_id,
                "task": task_id,
                "user": user_id,
            },
        )
    return project_id, user_id, task_id, session_id


def test_sqlite_upgrade_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "capture_sessions.db"
    config = _config(db_path)

    command.upgrade(config, "0065")
    engine = _engine(db_path)
    try:
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        assert {
            "id",
            "project_id",
            "code",
            "task_id",
            "batch_id",
            "operator_id",
            "robot_id",
            "location_id",
            "status",
            "started_at",
            "ended_at",
            "status_reason",
            "version",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        } <= set(columns)
        assert _TRIGGERS <= _sqlite_triggers(engine)

        project_id, user_id, task_id, session_id = _seed_sqlite(engine)

        # 初始状态非法：触发器拒绝。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO capture_sessions "
                        "(id, project_id, code, status, created_by, updated_by) "
                        "VALUES (:id, :project, 'S-BAD', 'RUNNING', :user, :user)"
                    ),
                    {"id": uuid4().hex, "project": project_id, "user": user_id},
                )

        # 合法迁移：SCHEDULED → RUNNING。
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE capture_sessions SET status = 'RUNNING', "
                    "started_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": session_id},
            )

        # 终态回退不受支持（RUNNING → SCHEDULED 不在白名单）。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE capture_sessions SET status = 'SCHEDULED', "
                        "started_at = NULL WHERE id = :id"
                    ),
                    {"id": session_id},
                )

        # 执行记录禁止删除。
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM capture_sessions WHERE id = :id"),
                    {"id": session_id},
                )

        # 回滚后行仍保留，任务未受影响。
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT status FROM capture_sessions WHERE id = :id"),
                    {"id": session_id},
                ).scalar_one()
                == "RUNNING"
            )
            assert (
                connection.execute(
                    text("SELECT status FROM collection_tasks WHERE id = :id"),
                    {"id": task_id},
                ).scalar_one()
                == "DRAFT"
            )
    finally:
        engine.dispose()

    command.downgrade(config, "0065")
    engine = _engine(db_path)
    try:
        assert TABLE not in inspect(engine).get_table_names()
        with engine.begin() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master " "WHERE type = 'trigger' AND tbl_name = :t"
                    ),
                    {"t": TABLE},
                )
            }
        assert not (names & _TRIGGERS)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        assert TABLE in inspect(engine).get_table_names()
        assert _TRIGGERS <= _sqlite_triggers(engine)
    finally:
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_capture_sessions_roundtrip_on_postgresql() -> None:
    schema = f"ddp_capture_session_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config_for_url(url)

        command.upgrade(config, "0065")
        engine = create_engine(url)
        try:
            assert TABLE not in inspect(engine).get_table_names()
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert TABLE in inspect(engine).get_table_names()
            project_id, user_id, task_id, session_id = (
                uuid4(),
                uuid4(),
                uuid4(),
                uuid4(),
            )
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
                    {"id": project_id, "code": schema[-20:], "name": "PG 项目"},
                )
                connection.execute(
                    text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
                    {"id": user_id, "name": "PG 操作员"},
                )
                connection.execute(
                    text(
                        "INSERT INTO collection_tasks "
                        "(id, project_id, code, name, status, priority, "
                        "created_by, updated_by) VALUES "
                        "(:id, :project, 'TASK-1', '任务', 'DRAFT', 'P1', :user, :user)"
                    ),
                    {"id": task_id, "project": project_id, "user": user_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO capture_sessions "
                        "(id, project_id, code, task_id, status, created_by, updated_by) "
                        "VALUES (:id, :project, 'S-1', :task, 'SCHEDULED', :user, :user)"
                    ),
                    {
                        "id": session_id,
                        "project": project_id,
                        "task": task_id,
                        "user": user_id,
                    },
                )

            # 初始状态非法：触发器拒绝。
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO capture_sessions "
                            "(id, project_id, code, status, created_by, updated_by) "
                            "VALUES (:id, :project, 'S-BAD', 'RUNNING', :user, :user)"
                        ),
                        {"id": uuid4(), "project": project_id, "user": user_id},
                    )

            # 合法迁移与删除守卫。
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE capture_sessions SET status = 'RUNNING', "
                        "started_at = now() WHERE id = :id"
                    ),
                    {"id": session_id},
                )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE capture_sessions SET status = 'SCHEDULED', "
                            "started_at = NULL WHERE id = :id"
                        ),
                        {"id": session_id},
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM capture_sessions WHERE id = :id"),
                        {"id": session_id},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, "0065")
        engine = create_engine(url)
        try:
            assert TABLE not in inspect(engine).get_table_names()
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert TABLE in inspect(engine).get_table_names()
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
