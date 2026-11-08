"""迁移测试：0002 可升级、可回滚、可再升级。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[2]
IDENTITY_TABLES = {"users", "projects", "project_members"}


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


def test_identity_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "migration.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    assert IDENTITY_TABLES <= _tables(db_path)

    command.downgrade(config, "0001")
    assert not IDENTITY_TABLES & _tables(db_path)

    command.upgrade(config, "head")
    assert IDENTITY_TABLES <= _tables(db_path)

    command.downgrade(config, "base")
    assert not IDENTITY_TABLES & _tables(db_path)
