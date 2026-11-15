"""G-07：calibration_profiles 行不可变（ADR-0020）。

用真实 SQL 绕过 ORM 直接改/删，验证**数据库层**真的拦住了——
应用层不提供 update 方法只是"我们不做"，触发器才是"谁都做不到"。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.infrastructure.db.base import Base
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get("DDP_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)

_NOW = datetime(2026, 11, 15, tzinfo=UTC)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    """独立 schema，跑完即删——不污染测试库里的其它表。"""
    schema = f"ddp_profile_it_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(_POSTGRES_URL, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _seed(engine) -> tuple[str, str]:
    project_id, user_id = str(uuid4()), str(uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into projects (id, code, name, created_at, updated_at) "
                "values (:id, :code, :name, :now, :now)"
            ),
            {"id": project_id, "code": f"p-{project_id[:8]}", "name": "项目", "now": _NOW},
        )
        conn.execute(
            text(
                "insert into users (id, display_name, created_at, updated_at) "
                "values (:id, :name, :now, :now)"
            ),
            {"id": user_id, "name": "用户", "now": _NOW},
        )
        conn.execute(
            text(
                "insert into calibration_profiles "
                "(id, project_id, name, scope, target_type, schema_version, "
                " required_types, selection_rule, version, created_at, updated_at, "
                " created_by, updated_by) "
                "values (:id, :pid, 'p1', 'SYSTEM', 'SENSOR', 'v1', '[]', '{}', 1, "
                " :now, :now, :uid, :uid)"
            ),
            {
                "id": str(uuid4()),
                "pid": project_id,
                "now": _NOW,
                "uid": user_id,
            },
        )
    return project_id, user_id


def test_profile_update_is_rejected_by_database(postgres_engine) -> None:
    """改配置必须插新版本行；直接 UPDATE 会让历史快照与配置脱节。"""
    engine = postgres_engine
    _seed(engine)

    with pytest.raises(IntegrityError, match="calibration_profile_immutable"):
        with engine.begin() as conn:
            conn.execute(text("update calibration_profiles set name = '改名'"))


def test_profile_delete_is_rejected_by_database(postgres_engine) -> None:
    """删除会让历史记录的 profile_id 悬空。"""
    engine = postgres_engine
    _seed(engine)

    with pytest.raises(IntegrityError, match="calibration_profile_delete_forbidden"):
        with engine.begin() as conn:
            conn.execute(text("delete from calibration_profiles"))


def test_inserting_new_version_is_allowed(postgres_engine) -> None:
    """不可变不等于不能演进：插入新版本行是允许且正确的做法。"""
    engine = postgres_engine
    project_id, user_id = _seed(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into calibration_profiles "
                "(id, project_id, name, scope, target_type, schema_version, "
                " required_types, selection_rule, version, created_at, updated_at, "
                " created_by, updated_by) "
                "values (:id, :pid, 'p1', 'SYSTEM', 'SENSOR', 'v1', '[]', '{}', 2, "
                " :now, :now, :uid, :uid)"
            ),
            {
                "id": str(uuid4()),
                "pid": project_id,
                "now": _NOW,
                "uid": user_id,
            },
        )
        count = conn.execute(
            text("select count(*) from calibration_profiles where project_id = :pid"),
            {"pid": project_id},
        ).scalar()
    assert count == 2
