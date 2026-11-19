"""迁移 0064：采集方案目标与质量阈值的上/下/再上往返。

覆盖三个不能靠人工验收的属性：

- 只 ``ADD COLUMN``：不重建 ``collection_plans``，因此素材状态守卫触发器与
  ``collection_tasks`` 到本表的复合外键在下回滚后仍然存在；
- 存量兼容：升级前的方案行 ``target_episode_count`` 为 ``NULL``（未设定），
  ``quality_thresholds`` 为 ``{}``（未指定），不伪造目标；
- 双向可迁移：``downgrade`` 后两列消失但方案行与触发器都在，再次升级成功。

SQLite 覆盖方言差异最大的路径；PostgreSQL 使用同一份原生 ``ADD/DROP COLUMN``。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "collection_plans"
COLUMNS = ("target_episode_count", "quality_thresholds")
PLAN_TRIGGERS = {
    f"trg_{TABLE}_initial_status",
    f"trg_{TABLE}_transition",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
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
                text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = :t"),
                {"t": TABLE},
            )
        }


def _seed_project_user_and_plan(engine: Engine) -> str:
    project_id, user_id, plan_id = uuid4().hex, uuid4().hex, uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects (id, code, name) VALUES (:id, :code, :name)"),
            {"id": project_id, "code": f"p-{project_id[:8]}", "name": "项目"},
        )
        connection.execute(
            text("INSERT INTO users (id, display_name) VALUES (:id, :name)"),
            {"id": user_id, "name": "创建人"},
        )
        connection.execute(
            text(
                "INSERT INTO collection_plans ("
                "id, project_id, code, name, plan_version, status, description, owner_id,"
                " version, created_by, updated_by"
                ") VALUES ("
                ":id, :project, 'PLAN-1', '历史方案', 1, 'DRAFT', NULL, NULL, 1, :user, :user"
                ")"
            ),
            {"id": plan_id, "project": project_id, "user": user_id},
        )
    return plan_id


def _read_targets(engine: Engine, plan_id: str) -> tuple[object, object]:
    table = sa.table(
        TABLE,
        sa.column("id", sa.String()),
        sa.column("target_episode_count", sa.Integer()),
        sa.column("quality_thresholds", sa.JSON()),
    )
    with engine.begin() as connection:
        row = connection.execute(
            sa.select(table.c.target_episode_count, table.c.quality_thresholds).where(
                table.c.id == plan_id
            )
        ).one()
        return row[0], row[1]


def test_sqlite_roundtrip_adds_columns_without_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "plan_targets.db"
    config = _config(db_path)

    command.upgrade(config, "0063")
    engine = _engine(db_path)
    try:
        assert not set(COLUMNS) & set(_columns(engine))
        plan_id = _seed_project_user_and_plan(engine)
        triggers_before = _sqlite_triggers(engine)
        assert PLAN_TRIGGERS <= triggers_before
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        columns = _columns(engine)
        assert set(COLUMNS) <= set(columns)
        # 目标可空：NULL 表示"未设定"，与 0 区分。
        assert columns["target_episode_count"]["nullable"] is True
        # 阈值非空且有默认值：存量行升级后是"未指定"而非 NULL。
        assert columns["quality_thresholds"]["nullable"] is False

        # 存量不伪造：历史方案没有目标，也没有阈值。
        target, thresholds = _read_targets(engine, plan_id)
        assert target is None
        assert thresholds == {}

        # 新列可写、可读。
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE collection_plans SET target_episode_count = 20, "
                    "quality_thresholds = :thresholds WHERE id = :id"
                ),
                {
                    "thresholds": '{"min_selected_count": 20, "max_duplicated_count": 0}',
                    "id": plan_id,
                },
            )
        assert _read_targets(engine, plan_id) == (
            20,
            {"min_selected_count": 20, "max_duplicated_count": 0},
        )
    finally:
        engine.dispose()

    command.downgrade(config, "0063")
    engine = _engine(db_path)
    try:
        assert not set(COLUMNS) & set(_columns(engine))
        # 原生 ADD/DROP COLUMN 不重建表：方案行、素材状态守卫触发器与
        # 任务到本表的复合外键都必须保持原样。
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT code FROM collection_plans WHERE id = :id"),
                    {"id": plan_id},
                ).scalar_one()
                == "PLAN-1"
            )
        assert PLAN_TRIGGERS <= _sqlite_triggers(engine)
        task_fks = {item["name"] for item in inspect(engine).get_foreign_keys("collection_tasks")}
        assert "fk_collection_tasks_project_plan" in task_fks
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(db_path)
    try:
        assert set(COLUMNS) <= set(_columns(engine))
        # 回滚丢弃升级后写入的计划值；再次升级不会恢复它们（与其它加列迁移一致）。
        assert _read_targets(engine, plan_id) == (None, {})
    finally:
        engine.dispose()
