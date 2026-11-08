"""迁移 0017 升降级与数据集触发器测试。"""

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
DATASET_TABLES = {
    "datasets",
    "dataset_versions",
    "dataset_items",
    "dataset_dedup_policies",
    "dataset_split_policies",
    "dataset_leakage_policies",
    "dataset_quality_policies",
    "dataset_leakage_reports",
    "dataset_quality_reports",
}
SHA = "sha256:" + "a" * 64


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


def test_dataset_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "dataset.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    assert DATASET_TABLES <= _tables(db_path)
    command.downgrade(config, "0016")
    assert not DATASET_TABLES & _tables(db_path)
    command.upgrade(config, "head")
    assert DATASET_TABLES <= _tables(db_path)


def _seed_project_user(connection) -> tuple[str, str]:
    project_id, user_id = uuid4().hex, uuid4().hex
    connection.execute(
        text("INSERT INTO projects (id, code, name) VALUES (:id, :code, '项目')"),
        {"id": project_id, "code": f"p-{project_id[:8]}"},
    )
    connection.execute(
        text("INSERT INTO users (id, display_name) VALUES (:id, '用户')"),
        {"id": user_id},
    )
    return project_id, user_id


def _insert_dataset(connection, project_id: str, user_id: str) -> tuple[str, str]:
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
    return dataset_id, lineage_id


def _insert_version(connection, project_id: str, user_id: str, dataset_id: str) -> str:
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
            "lineage": uuid4().hex,
            "sha": SHA,
            "p1": uuid4().hex,
            "p2": uuid4().hex,
            "p3": uuid4().hex,
            "p4": uuid4().hex,
            "user": user_id,
        },
    )
    return version_id


def test_dataset_triggers_reject_illegal_direct_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "dataset_triggers.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            project_id, user_id = _seed_project_user(connection)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO datasets ("
                        "id, project_id, dataset_lineage_id, code, name, purpose, owner_id, "
                        "status, tags, usage_policy, frozen_version_count, version, "
                        "created_by, updated_by"
                        ") VALUES (:id, :project_id, :lineage, 'bad', '坏', 'TRAINING', "
                        ":owner, 'ACTIVE', '[]', 'TRAINING_ALLOWED', 0, 1, :owner, :owner)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project_id": project_id,
                        "lineage": uuid4().hex,
                        "owner": user_id,
                    },
                )
        with engine.begin() as connection:
            dataset_id, _lineage = _insert_dataset(connection, project_id, user_id)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE datasets SET status = 'ACTIVE' WHERE id = :id"),
                {"id": dataset_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE datasets SET code = 'changed' WHERE id = :id"),
                    {"id": dataset_id},
                )
        with engine.begin() as connection:
            version_id = _insert_version(connection, project_id, user_id, dataset_id)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE dataset_versions SET status = 'FROZEN' WHERE id = :id"),
                    {"id": version_id},
                )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE dataset_versions SET status = 'CANCELLED' WHERE id = :id"),
                {"id": version_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE dataset_versions SET manifest_sha256 = :sha WHERE id = :id"),
                    {"id": version_id, "sha": SHA},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM datasets WHERE id = :id"),
                    {"id": dataset_id},
                )
    finally:
        engine.dispose()


_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get("DDP_DATABASE_URL")


@pytest.mark.skipif(
    not _POSTGRES_URL or not _POSTGRES_URL.startswith("postgres"),
    reason="未配置 DDP_TEST_POSTGRES_URL，PostgreSQL 触发器回归 BLOCKED",
)
def test_postgres_dataset_guards_installed() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", _POSTGRES_URL)
    command.upgrade(config, "head")
    engine = create_engine(_POSTGRES_URL)
    try:
        inspector = inspect(engine)
        assert DATASET_TABLES <= set(inspector.get_table_names())
        with engine.connect() as connection:
            triggers = set(
                connection.execute(
                    text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
                ).scalars()
            )
        assert "trg_dataset_versions_transition" in triggers
        assert "trg_dataset_versions_immutable" in triggers
    finally:
        engine.dispose()
