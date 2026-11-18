"""迁移 0055：导入阶段/进度列与步骤日志表在 SQLite 上升降级可逆。

**为什么单独测**

``data_source_imports`` 是已有表，``batch_alter_table`` 在 SQLite 上走
"建新表→拷数据→换名"的重建路径；只写迁移不验证，很容易出现"本地老库绿、
全新库 upgrade 失败"。本用例从空库升级到 head，再回滚到 0054，显式检查：

- 三列存在且可空（NULL 表示未测量，不能 NOT NULL 默认 0）；
- CHECK 约束真的进了库；
- 日志表、索引与级联外键存在；
- 回滚把列、约束与日志表清干净。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def test_upgrade_adds_nullable_progress_columns_and_log_table(tmp_path: Path) -> None:
    db_path = tmp_path / "import_progress.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]: column for column in inspector.get_columns("data_source_imports")
        }
        assert {"stage", "processed_items", "total_items"} <= set(columns)
        # 存量必须保持 NULL：三列都不能是 NOT NULL + 默认 0。
        for name in ("stage", "processed_items", "total_items"):
            assert columns[name]["nullable"] is True, (name, columns[name])

        with engine.connect() as conn:
            import_ddl = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='data_source_imports'"
                )
            ).scalar_one()
            log_ddl = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='data_source_import_logs'"
                )
            ).scalar_one()
            indexes = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='data_source_import_logs'"
                    )
                )
            }

        assert "import_stage_allowed" in import_ddl
        assert "import_progress_within_total" in import_ddl
        assert "import_processed_items_non_negative" in import_ddl
        assert "import_total_items_non_negative" in import_ddl
        assert "import_log_stage_allowed" in log_ddl
        assert "import_log_level_allowed" in log_ddl
        assert "import_log_message_not_empty" in log_ddl
        assert "ix_data_source_import_logs_import" in indexes
    finally:
        engine.dispose()


def test_downgrade_removes_columns_constraints_and_log_table(tmp_path: Path) -> None:
    db_path = tmp_path / "import_progress_down.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0054")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        column_names = {column["name"] for column in inspector.get_columns("data_source_imports")}
        table_names = set(inspector.get_table_names())
        with engine.connect() as conn:
            import_ddl = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='data_source_imports'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert not ({"stage", "processed_items", "total_items"} & column_names)
    assert "data_source_import_logs" not in table_names
    assert "import_stage_allowed" not in import_ddl
    assert "import_progress_within_total" not in import_ddl
