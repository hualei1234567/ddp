"""迁移 0021（阶段 5）与 0022（阶段 6）的升降级回归。

此前只有人工执行记录，CI 的 ``downgrade 0003`` 覆盖不到这两版；
本用例补齐自动化：建表 → 降级删除 → 再升级恢复，并校验账本禁改触发器。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]

STAGE5_TABLES = {
    "models",
    "model_versions",
    "evaluations",
    "inference_services",
    "inference_tasks",
    "model_feedback",
    "data_requirements",
    "model_callbacks",
}

STAGE6_TABLES = {
    "suppliers",
    "supplier_delivery_batches",
    "inbound_segments",
    "acceptance_ledger_entries",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _tables(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_stage5_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "stage5.db"
    config = _config(db_path)
    command.upgrade(config, "0021")
    assert STAGE5_TABLES <= _tables(db_path)
    command.downgrade(config, "0020")
    assert not STAGE5_TABLES & _tables(db_path)
    command.upgrade(config, "head")
    assert STAGE5_TABLES <= _tables(db_path)


def test_stage6_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "stage6.db"
    config = _config(db_path)
    command.upgrade(config, "0022")
    tables = _tables(db_path)
    assert STAGE6_TABLES <= tables
    command.downgrade(config, "0021")
    remaining = _tables(db_path)
    assert not STAGE6_TABLES & remaining
    command.upgrade(config, "head")
    assert STAGE6_TABLES <= _tables(db_path)


def test_stage6_ledger_trigger_installed(tmp_path: Path) -> None:
    """迁移必须安装账本禁改触发器，而不能只在 create_all 路径生效。"""
    db_path = tmp_path / "stage6_guard.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' AND tbl_name='acceptance_ledger_entries'"
                    )
                )
            }
        expected = {
            "trg_acceptance_ledger_entries_update",
            "trg_acceptance_ledger_entries_delete",
        }
        assert expected <= names
    finally:
        engine.dispose()
