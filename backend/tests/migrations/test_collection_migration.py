"""迁移测试：0006/0007 可升级/回滚，且数据库兜底触发器生效。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
STAGE2_TABLES = {
    "audit_logs",
    "outbox_events",
    "idempotency_records",
    "collection_tasks",
    "collection_batches",
    "collection_task_devices",
    "collection_task_sensors",
}
STAGE1_TABLES = {"users", "projects", "project_members", "devices", "sensors"}


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


def test_collection_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "collection.db"
    config = _config(db_path)

    command.upgrade(config, "head")
    tables = _tables(db_path)
    assert STAGE2_TABLES <= tables
    assert STAGE1_TABLES <= tables

    command.downgrade(config, "0005")
    tables = _tables(db_path)
    assert not STAGE2_TABLES & tables
    assert STAGE1_TABLES <= tables

    command.upgrade(config, "head")
    assert STAGE2_TABLES <= _tables(db_path)


def test_collection_migration_creates_contract_constraints(tmp_path: Path) -> None:
    db_path = tmp_path / "constraints.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        task_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("collection_tasks")
        }
        assert ("project_id", "code") in task_uniques
        assert ("project_id", "id") in task_uniques

        batch_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("collection_batches")
        }
        assert ("project_id", "task_id", "sequence_no") in batch_uniques

        idempotency_pk = inspector.get_pk_constraint("idempotency_records")
        assert tuple(idempotency_pk["constrained_columns"]) == ("project_id", "user_id", "key")

        task_checks = {item["name"] for item in inspector.get_check_constraints("collection_tasks")}
        assert "ck_collection_tasks_status_allowed" in task_checks
        assert "ck_collection_tasks_version_positive" in task_checks
    finally:
        engine.dispose()


def test_migration_installs_state_and_audit_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "triggers.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    project_id = uuid4().hex
    user_id = uuid4().hex
    task_id = uuid4().hex
    batch_id = uuid4().hex
    audit_id = uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, code, name) VALUES (:id, 'p1', '项目一')"),
                {"id": project_id},
            )
            connection.execute(
                text("INSERT INTO users (id, display_name) VALUES (:id, '用户一')"),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO collection_tasks ("
                    "id, project_id, code, name, status, priority, quality_summary, "
                    "created_by, updated_by"
                    ") VALUES (:id, :project_id, 'T-1', '任务一', 'DRAFT', 'P1', '{}', "
                    ":user_id, :user_id)"
                ),
                {
                    "id": task_id,
                    "project_id": project_id,
                    "user_id": user_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO collection_batches ("
                    "id, project_id, task_id, code, sequence_no, status, "
                    "created_by, updated_by"
                    ") VALUES (:id, :project_id, :task_id, 'B-1', 1, 'PLANNED', "
                    ":user_id, :user_id)"
                ),
                {
                    "id": batch_id,
                    "project_id": project_id,
                    "task_id": task_id,
                    "user_id": user_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO audit_logs ("
                    "id, project_id, actor_user_id, resource_type, resource_id, action, "
                    "result, details"
                    ") VALUES (:id, :project_id, :user_id, 'COLLECTION_TASK', :task_id, "
                    "'create', 'SUCCESS', '{}')"
                ),
                {
                    "id": audit_id,
                    "project_id": project_id,
                    "user_id": user_id,
                    "task_id": task_id,
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE collection_tasks SET status = 'APPROVED' WHERE id = :id"),
                    {"id": task_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE collection_batches SET status = 'READY' WHERE id = :id"),
                    {"id": batch_id},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
                    {"id": audit_id},
                )

        # 合法迁移仍可通过。
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE collection_tasks SET status = 'PENDING_REVIEW' WHERE id = :id"),
                {"id": task_id},
            )
    finally:
        engine.dispose()
