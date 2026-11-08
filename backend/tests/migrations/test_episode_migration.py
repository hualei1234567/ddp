"""迁移测试：0009/0010 IngestionJob/Episode/血缘与触发器可升级、回滚。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

BACKEND_DIR = Path(__file__).resolve().parents[2]
NEW_TABLES = {
    "ingestion_jobs",
    "episodes",
    "episode_raw_assets",
    "lineage_node_refs",
    "data_lineage_edges",
}
PREVIOUS_TABLES = {
    "upload_sessions",
    "upload_parts",
    "raw_assets",
    "raw_asset_links",
    "collection_tasks",
    "collection_batches",
}
TRIGGERS = {
    "trg_ingestion_jobs_transition",
    "trg_episodes_transition",
    "trg_episodes_frozen_fields",
    "trg_episode_raw_assets_no_update",
    "trg_data_lineage_edges_no_delete",
    "trg_data_lineage_edges_supersedes_linear",
}


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def test_episode_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "episode.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert NEW_TABLES <= tables
        assert PREVIOUS_TABLES <= tables
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        assert TRIGGERS <= names
    finally:
        engine.dispose()

    command.downgrade(config, "0008")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert not NEW_TABLES & tables
        assert PREVIOUS_TABLES <= tables
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        assert not TRIGGERS & names
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        assert NEW_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_episode_migration_contract_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "episode_constraints.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)

        episode_uniques = {
            tuple(item["column_names"]) for item in inspector.get_unique_constraints("episodes")
        }
        assert ("project_id", "id") in episode_uniques
        assert ("project_id", "code") in episode_uniques
        assert ("project_id", "episode_lineage_id", "episode_version") in episode_uniques

        asset_pk = inspector.get_pk_constraint("episode_raw_assets")
        assert tuple(asset_pk["constrained_columns"]) == (
            "project_id",
            "episode_id",
            "asset_id",
            "role",
        )

        job_indexes = {item["name"]: item for item in inspector.get_indexes("ingestion_jobs")}
        idempotency = job_indexes["uq_ingestion_jobs_idempotency"]
        assert idempotency["unique"] in (True, 1)
        assert tuple(idempotency["column_names"]) == ("project_id", "idempotency_key")

        episode_indexes = {item["name"]: item for item in inspector.get_indexes("episodes")}
        supersedes = episode_indexes["uq_episodes_supersedes"]
        assert supersedes["unique"] in (True, 1)
        assert tuple(supersedes["column_names"]) == ("project_id", "supersedes_id")

        lineage_fks = inspector.get_foreign_keys("data_lineage_edges")
        assert any(
            item["constrained_columns"] == ["project_id", "from_node_type", "from_node_id"]
            and item["referred_table"] == "lineage_node_refs"
            for item in lineage_fks
        )

        episode_checks = {item["name"] for item in inspector.get_check_constraints("episodes")}
        assert "ck_episodes_duration_consistent" in episode_checks
        assert "ck_episodes_ready_requires_manifest" in episode_checks
        job_checks = {item["name"] for item in inspector.get_check_constraints("ingestion_jobs")}
        assert "ck_ingestion_jobs_failed_requires_error" in job_checks
    finally:
        engine.dispose()
