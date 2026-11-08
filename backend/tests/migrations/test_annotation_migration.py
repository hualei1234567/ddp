"""迁移测试：0012/0013 升降级与 SQLite 触发器兜底。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
STAGE3_TABLES = {
    "annotation_schemas",
    "annotation_sample_sets",
    "annotation_sample_episodes",
    "annotation_tasks",
    "annotation_batches",
    "annotation_assignments",
    "annotation_assignment_events",
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


def test_annotation_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "annotation.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    assert STAGE3_TABLES <= _tables(db_path)
    command.downgrade(config, "0011")
    assert not STAGE3_TABLES & _tables(db_path)
    command.upgrade(config, "head")
    assert STAGE3_TABLES <= _tables(db_path)


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


def _insert_schema(connection, project_id: str, user_id: str, schema_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO annotation_schemas ("
            "id, project_id, schema_lineage_id, schema_version, code, name, status, "
            "annotation_levels, labels, compatibility, schema_sha256, created_by, updated_by"
            ") VALUES (:id, :project_id, :lineage_id, 1, :code, 'Schema', 'DRAFT', "
            '\'["EPISODE"]\', \'[{"label_key": "action", "label_type": "ENUM", '
            '"required": true, "level": "EPISODE", "visibility": "ANNOTATOR", '
            '"value_spec": {"enum_values": ["PICK"]}}]\', '
            "'BACKWARD_COMPATIBLE', :sha, :user_id, :user_id)"
        ),
        {
            "id": schema_id,
            "project_id": project_id,
            "lineage_id": uuid4().hex,
            "code": f"s-{schema_id[:8]}",
            "sha": SHA,
            "user_id": user_id,
        },
    )


def _publish_schema(connection, schema_id: str) -> None:
    connection.execute(
        text("UPDATE annotation_schemas SET status = 'PENDING_REVIEW' WHERE id = :id"),
        {"id": schema_id},
    )
    connection.execute(
        text(
            "UPDATE annotation_schemas SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP "
            "WHERE id = :id"
        ),
        {"id": schema_id},
    )


def _insert_sample_set(
    connection,
    project_id: str,
    user_id: str,
    sample_set_id: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO annotation_sample_sets ("
            "id, project_id, code, status, selection_rule_version, strategy, filters, strata, "
            "dedup_key, candidate_count, selected_count, excluded_count, deduplicated_count, "
            "data_source, exclusion_summary, created_by, updated_by"
            ") VALUES (:id, :project_id, :code, 'DRAFT', 'selection-v1', 'FILTER', '{}', '[]', "
            "'EPISODE_LINEAGE', 1, 0, 0, 0, 'UNKNOWN', '{}', :user_id, :user_id)"
        ),
        {
            "id": sample_set_id,
            "project_id": project_id,
            "code": f"sample-{sample_set_id[:8]}",
            "user_id": user_id,
        },
    )
    connection.execute(
        text("UPDATE annotation_sample_sets SET status = 'PREVIEW' WHERE id = :id"),
        {"id": sample_set_id},
    )
    connection.execute(
        text(
            "UPDATE annotation_sample_sets SET status = 'SNAPSHOTTED', "
            "snapshot_sha256 = :sha, snapshot_at = CURRENT_TIMESTAMP WHERE id = :id"
        ),
        {"id": sample_set_id, "sha": SHA},
    )


def _insert_task(
    connection,
    project_id: str,
    user_id: str,
    sample_set_id: str,
    schema_id: str,
) -> str:
    task_id = uuid4().hex
    connection.execute(
        text(
            "INSERT INTO annotation_tasks ("
            "id, project_id, code, name, status, sample_set_id, schema_id, schema_version, "
            "schema_sha256, iaa_policy, quality_policy, assignment_mode, target_item_count, "
            "target_episode_count, priority, progress, quality_summary, created_by, updated_by"
            ") VALUES (:id, :project_id, :code, '任务', 'DRAFT', :sample_set_id, :schema_id, 1, "
            ":sha, '{}', '{}', 'MANUAL', 1, 1, 'P1', '{}', '{}', :user_id, :user_id)"
        ),
        {
            "id": task_id,
            "project_id": project_id,
            "code": f"t-{uuid4().hex[:8]}",
            "sample_set_id": sample_set_id,
            "schema_id": schema_id,
            "sha": SHA,
            "user_id": user_id,
        },
    )
    return task_id


def test_annotation_triggers_reject_illegal_direct_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "annotation_triggers.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            project_id, user_id = _seed_project_user(connection)

        schema_id, sample_set_id = uuid4().hex, uuid4().hex
        task_id = ""
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO annotation_schemas ("
                        "id, project_id, schema_lineage_id, schema_version, code, name, status, "
                        "annotation_levels, labels, compatibility, schema_sha256, "
                        "created_by, updated_by"
                        ") VALUES (:id, :project_id, :lineage, 1, 'bad', '坏', 'PUBLISHED', "
                        "'[]', '[]', 'BREAKING', :sha, :user_id, :user_id)"
                    ),
                    {
                        "id": uuid4().hex,
                        "project_id": project_id,
                        "lineage": uuid4().hex,
                        "sha": SHA,
                        "user_id": user_id,
                    },
                )
        with engine.begin() as connection:
            _insert_schema(connection, project_id, user_id, schema_id)
            _publish_schema(connection, schema_id)
            _insert_sample_set(connection, project_id, user_id, sample_set_id)
            task_id = _insert_task(connection, project_id, user_id, sample_set_id, schema_id)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE annotation_schemas SET status = 'DRAFT' WHERE id = :id"),
                    {"id": schema_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE annotation_sample_sets SET filters = '{\"x\": 1}' WHERE id = :id"),
                    {"id": sample_set_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE annotation_tasks SET status = 'APPROVED' WHERE id = :id"),
                    {"id": task_id},
                )
    finally:
        engine.dispose()


def test_annotation_task_binding_requires_snapshot_and_published_schema(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "annotation_binding.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            project_id, user_id = _seed_project_user(connection)
            schema_id = uuid4().hex
            _insert_schema(connection, project_id, user_id, schema_id)
            _publish_schema(connection, schema_id)
            draft_sample = uuid4().hex
            connection.execute(
                text(
                    "INSERT INTO annotation_sample_sets ("
                    "id, project_id, code, status, selection_rule_version, strategy, filters, "
                    "strata, dedup_key, candidate_count, selected_count, excluded_count, "
                    "deduplicated_count, data_source, exclusion_summary, created_by, updated_by"
                    ") VALUES (:id, :project_id, :code, 'DRAFT', 'selection-v1', 'FILTER', "
                    "'{}', '[]', 'EPISODE_LINEAGE', 0, 0, 0, 0, 'UNKNOWN', '{}', "
                    ":user_id, :user_id)"
                ),
                {
                    "id": draft_sample,
                    "project_id": project_id,
                    "code": f"draft-{draft_sample[:8]}",
                    "user_id": user_id,
                },
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_task(connection, project_id, user_id, draft_sample, schema_id)
    finally:
        engine.dispose()
