"""迁移测试：0046 项目描述/状态/版本列可升级、可回滚，且不丢存量项目。

关键断言（踩过的坑都在这里）：

- SQLite 加 CHECK 必须重建表，``uq_projects_code`` 唯一约束不能丢；
- 存量行升级后 ``status='ACTIVE'`` / ``version=1`` / ``description IS NULL``；
- downgrade 必须能回到 0043 并保留项目行，再 upgrade 仍成功。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "0043"


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _columns(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {column["name"] for column in inspect(engine).get_columns("projects")}
    finally:
        engine.dispose()


def _check_names(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {str(item["name"]) for item in inspect(engine).get_check_constraints("projects")}
    finally:
        engine.dispose()


def _insert_project(db_path: Path, project_id: uuid.UUID, code: str) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO projects (id, code, name) " "VALUES (:id, :code, :name)"),
                {"id": project_id.hex, "code": code, "name": "存量项目"},
            )
    finally:
        engine.dispose()


def test_project_management_migration_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "projects.db"
    config = _config(db_path)
    project_id = uuid.uuid4()

    command.upgrade(config, PREVIOUS_REVISION)
    assert "status" not in _columns(db_path)
    _insert_project(db_path, project_id, "legacy-project")

    command.upgrade(config, "head")
    columns = _columns(db_path)
    assert {"description", "status", "version"} <= columns
    checks = _check_names(db_path)
    # 约束名与 ORM ``create_all`` 路径一致：ck_projects_*，不是 ck_projects_ck_projects_*。
    assert "ck_projects_status_allowed" in checks
    assert "ck_projects_version_positive" in checks

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        indexes = {index["name"] for index in inspector.get_indexes("projects")}
        assert "ix_projects_status_code" in indexes
        unique_names = {
            constraint["name"] for constraint in inspector.get_unique_constraints("projects")
        }
        # 重建表不能丢掉 0002 建的唯一约束。
        assert any(name and "uq_projects_code" in name for name in unique_names)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT code, name, description, status, version "
                    "FROM projects WHERE id = :id"
                ),
                {"id": project_id.hex},
            ).one()
        assert row.code == "legacy-project"
        assert row.name == "存量项目"
        assert row.description is None
        assert row.status == "ACTIVE"
        assert row.version == 1
    finally:
        engine.dispose()

    # DB 兜底：非法状态与非法版本都被 CHECK 拒绝。
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO projects (id, code, name, status) "
                        "VALUES (:id, :code, :name, 'DELETED')"
                    ),
                    {"id": uuid.uuid4().hex, "code": "bad-status", "name": "x"},
                )
    finally:
        engine.dispose()
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO projects (id, code, name, version) "
                        "VALUES (:id, :code, :name, 0)"
                    ),
                    {"id": uuid.uuid4().hex, "code": "bad-version", "name": "x"},
                )
    finally:
        engine.dispose()

    command.downgrade(config, PREVIOUS_REVISION)
    assert "status" not in _columns(db_path)
    assert "version" not in _columns(db_path)
    assert "description" not in _columns(db_path)

    # 回滚不丢存量项目，且可以再次升级。
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM projects WHERE code = 'legacy-project'")
            ).scalar_one()
        assert count == 1
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    assert {"description", "status", "version"} <= _columns(db_path)
