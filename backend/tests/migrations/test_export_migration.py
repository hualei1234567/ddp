"""迁移 0018 升降级与 ExportJob 触发器测试。"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
EXPORT_TABLES = {"export_jobs"}
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


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


def test_export_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "export.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    assert EXPORT_TABLES <= _tables(db_path)
    command.downgrade(config, "0017")
    assert not EXPORT_TABLES & _tables(db_path)
    command.upgrade(config, "head")
    assert EXPORT_TABLES <= _tables(db_path)


def _seed_prerequisites(connection) -> tuple[str, str, str]:
    project_id, user_id = uuid4().hex, uuid4().hex
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, '项目')"),
        {"id": project_id, "code": f"p-{project_id[:8]}"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, '用户')"),
        {"id": user_id},
    )
    dataset_id, lineage_id = uuid4().hex, uuid4().hex
    connection.execute(
        text(
            "INSERT INTO datasets ("
            "id, project_id, dataset_lineage_id, code, name, purpose, owner_id, status, "
            "tags, usage_policy, frozen_version_count, version, created_at, updated_at, "
            "created_by, updated_by"
            ") VALUES (:id, :project_id, :lineage, :code, '数据集', 'TRAINING', :owner, "
            "'DRAFT', '[]', 'TRAINING_ALLOWED', 0, 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, :owner, :owner)"
        ),
        {
            "id": dataset_id,
            "project_id": project_id,
            "lineage": lineage_id,
            "code": f"ds-{dataset_id[:8]}",
            "owner": user_id,
        },
    )
    version_id = uuid4().hex
    connection.execute(
        text(
            "INSERT INTO dataset_versions ("
            "id, project_id, dataset_id, dataset_lineage_id, dataset_version, status, "
            "validity_status, selection_rule_version, selection_filters, "
            "selection_snapshot_at, dedup_policy_id, dedup_policy_version, "
            "dedup_policy_sha256, split_policy_id, split_policy_version, "
            "split_policy_sha256, leakage_policy_id, leakage_policy_version, "
            "leakage_policy_sha256, quality_policy_id, quality_policy_version, "
            "quality_policy_sha256, candidate_count, selected_count, excluded_count, "
            "duplicated_count, item_count, train_count, validation_count, test_count, "
            "annotation_schema_versions, blocking_issues, warnings, exclusion_summary, "
            "build_attempt, version, created_at, updated_at, created_by, updated_by"
            ") VALUES ("
            ":id, :project_id, :dataset_id, :lineage, 1, 'DRAFT', 'VALID', 'sel/1', '{}', "
            "CURRENT_TIMESTAMP, :p1, 1, :sha, :p2, 1, :sha, :p3, 1, :sha, :p4, 1, :sha, "
            "0, 0, 0, 0, 0, 0, 0, 0, '[]', '[]', '[]', '{}', 0, 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, :user, :user)"
        ),
        {
            "id": version_id,
            "project_id": project_id,
            "dataset_id": dataset_id,
            "lineage": lineage_id,
            "sha": SHA,
            "p1": uuid4().hex,
            "p2": uuid4().hex,
            "p3": uuid4().hex,
            "p4": uuid4().hex,
            "user": user_id,
        },
    )
    return project_id, user_id, version_id


def _insert_job(
    connection,
    project_id: str,
    user_id: str,
    version_id: str,
    *,
    status: str = "QUEUED",
    job_id: str | None = None,
) -> str:
    identifier = job_id or uuid4().hex
    connection.execute(
        text(
            "INSERT INTO export_jobs ("
            "id, project_id, dataset_version_id, status, format, format_version, "
            "exporter_id, exporter_version, mapping_version, config, config_sha256, "
            "idempotency_key, requested_by, output_prefix, attempt, max_attempts, "
            "timeout_seconds, verification_status, version, created_at, updated_at, "
            "created_by, updated_by"
            ") VALUES ("
            ":id, :project_id, :version_id, :status, 'LE_ROBOT', 'ddp.lerobot/1.0.0', "
            "'exporter', '1.0.0', 'ddp.lerobot/1.0.0', '{}', :sha, :key, :user, "
            ":prefix, 0, 3, 60, 'UNVERIFIED', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
            ":user, :user)"
        ),
        {
            "id": identifier,
            "project_id": project_id,
            "version_id": version_id,
            "status": status,
            "sha": SHA,
            "key": f"key-{identifier[:8]}",
            "user": user_id,
            "prefix": f"exports/{project_id}/{version_id}/{identifier}/",
        },
    )
    return identifier


def test_export_triggers_reject_illegal_direct_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "export_triggers.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            project_id, user_id, version_id = _seed_prerequisites(connection)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_job(
                    connection,
                    project_id,
                    user_id,
                    version_id,
                    status="RUNNING",
                )

        with engine.begin() as connection:
            job_id = _insert_job(connection, project_id, user_id, version_id)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE export_jobs SET status = 'RUNNING', "
                    "attempt = 1, started_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": job_id},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE export_jobs SET dataset_version_id = :other WHERE id = :id"),
                    {"id": job_id, "other": uuid4().hex},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE export_jobs SET status = 'QUEUED' WHERE id = :id"),
                    {"id": job_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE export_jobs SET status = 'SUCCEEDED' WHERE id = :id"),
                    {"id": job_id},
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE export_jobs SET status = 'SUCCEEDED', "
                    "finished_at = CURRENT_TIMESTAMP, artifact_count = 4, total_bytes = 100, "
                    "artifact_manifest_key = :manifest, "
                    "artifact_manifest_sha256 = :sha, aggregate_sha256 = :sha_b "
                    "WHERE id = :id"
                ),
                {
                    "id": job_id,
                    "manifest": f"exports/{project_id}/manifest.json",
                    "sha": SHA,
                    "sha_b": SHA_B,
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE export_jobs SET aggregate_sha256 = :sha WHERE id = :id"),
                    {"id": job_id, "sha": SHA},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM export_jobs WHERE id = :id"),
                    {"id": job_id},
                )
    finally:
        engine.dispose()


_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get("DDP_DATABASE_URL")


@pytest.mark.skipif(
    not _POSTGRES_URL or not _POSTGRES_URL.startswith("postgres"),
    reason="未配置 DDP_TEST_POSTGRES_URL，PostgreSQL 触发器回归 BLOCKED",
)
def test_postgres_export_guards_installed() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", _POSTGRES_URL)
    command.upgrade(config, "head")
    engine = create_engine(_POSTGRES_URL)
    try:
        inspector = inspect(engine)
        assert "export_jobs" in inspector.get_table_names()
        with engine.connect() as connection:
            triggers = set(
                connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
                ).scalars()
            )
        assert "trg_export_jobs_transition" in triggers
        assert "trg_export_jobs_input_immutable" in triggers
        assert "trg_export_jobs_no_delete" in triggers
    finally:
        engine.dispose()
