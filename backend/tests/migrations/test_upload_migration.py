"""迁移测试：0008 上传会话/分片/RawAsset/使用关系可升级与回滚。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[2]
UPLOAD_TABLES = {
    "upload_sessions",
    "upload_parts",
    "raw_assets",
    "raw_asset_links",
}
PREVIOUS_TABLES = {
    "collection_tasks",
    "collection_batches",
    "audit_logs",
    "idempotency_records",
}
STAGE1_TABLES = {"users", "projects", "project_members", "devices", "sensors"}
CHECKSUM_BINDING_TABLE = "upload_part_checksum_bindings"


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def test_upload_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "upload.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert UPLOAD_TABLES <= tables
        assert PREVIOUS_TABLES <= tables
        assert STAGE1_TABLES <= tables
    finally:
        engine.dispose()

    command.downgrade(config, "0007")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert not UPLOAD_TABLES & tables
        assert PREVIOUS_TABLES <= tables
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert UPLOAD_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_checksum_binding_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "checksum_binding.db"
    config = _config(db_path)

    command.upgrade(config, "0011")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        assert CHECKSUM_BINDING_TABLE in inspector.get_table_names()

        pk = inspector.get_pk_constraint(CHECKSUM_BINDING_TABLE)
        assert tuple(pk["constrained_columns"]) == (
            "project_id",
            "upload_session_id",
            "part_number",
        )
        fks = inspector.get_foreign_keys(CHECKSUM_BINDING_TABLE)
        assert any(
            item["constrained_columns"] == ["project_id", "upload_session_id"]
            and item["referred_table"] == "upload_sessions"
            for item in fks
        )
        checks = {item["name"] for item in inspector.get_check_constraints(CHECKSUM_BINDING_TABLE)}
        assert "ck_upload_part_checksum_bindings_sha256_format" in checks
        assert "ck_upload_part_checksum_bindings_part_number_range" in checks
    finally:
        engine.dispose()

    command.downgrade(config, "0010")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert CHECKSUM_BINDING_TABLE not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert CHECKSUM_BINDING_TABLE in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upload_migration_contract_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "upload_constraints.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        session_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("upload_sessions")
        }
        assert ("project_id", "id") in session_uniques

        part_pk = inspector.get_pk_constraint("upload_parts")
        assert tuple(part_pk["constrained_columns"]) == (
            "project_id",
            "upload_session_id",
            "part_number",
        )

        part_fks = inspector.get_foreign_keys("upload_parts")
        assert any(
            item["constrained_columns"] == ["project_id", "upload_session_id"]
            and item["referred_table"] == "upload_sessions"
            for item in part_fks
        )

        asset_indexes = {item["name"]: item for item in inspector.get_indexes("raw_assets")}
        content_index = asset_indexes["uq_raw_assets_project_content_active"]
        assert content_index["unique"] in (True, 1)
        assert tuple(content_index["column_names"]) == (
            "project_id",
            "sha256",
            "size_bytes",
        )

        asset_checks = {item["name"] for item in inspector.get_check_constraints("raw_assets")}
        assert "ck_raw_assets_status_allowed" in asset_checks
        assert "ck_raw_assets_sha256_format" in asset_checks

        link_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("raw_asset_links")
        }
        assert ("project_id", "id") in link_uniques
        link_indexes = {item["name"] for item in inspector.get_indexes("raw_asset_links")}
        assert "uq_raw_asset_links_session" in link_indexes
    finally:
        engine.dispose()
