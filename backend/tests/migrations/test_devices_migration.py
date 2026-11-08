"""迁移测试：0003 设备域可升级、可回滚，且约束与契约一致。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEVICE_TABLES = {
    "calibration_profiles",
    "robots",
    "devices",
    "calibration_records",
    "sensors",
}
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


def test_device_migration_upgrade_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "devices.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    assert DEVICE_TABLES <= _tables(db_path)

    command.downgrade(config, "0002")
    tables = _tables(db_path)
    assert not DEVICE_TABLES & tables
    assert IDENTITY_TABLES <= tables

    command.upgrade(config, "head")
    assert DEVICE_TABLES <= _tables(db_path)

    command.downgrade(config, "base")
    assert not (DEVICE_TABLES | IDENTITY_TABLES) & _tables(db_path)


def test_device_migration_creates_contract_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "constraints.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        for table in ("robots", "devices", "sensors"):
            unique_columns = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(table)
            }
            assert ("project_id", "code") in unique_columns
            assert ("project_id", "serial_number") in unique_columns

        calibration_indexes = {
            item["name"]: item
            for item in inspector.get_indexes("calibration_records")
        }
        active_index = calibration_indexes["uq_calibration_records_active_target"]
        assert bool(active_index["unique"]) is True
        assert tuple(active_index["column_names"]) == (
            "project_id",
            "scope",
            "target_type",
            "target_id",
            "calibration_type",
        )

        sensor_checks = {
            item["name"] for item in inspector.get_check_constraints("sensors")
        }
        assert "ck_sensors_mount_required" in sensor_checks
        assert "ck_sensors_sample_rate_positive" in sensor_checks
    finally:
        engine.dispose()
