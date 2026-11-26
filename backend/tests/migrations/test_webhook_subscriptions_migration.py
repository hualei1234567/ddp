"""Webhook 订阅表迁移测试（0085）。

覆盖：upgrade → 唯一约束 / CHECK 枚举 / secret_hash 长度 → downgrade → 再 upgrade。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[sa.Engine]:
    url = f"sqlite+pysqlite:///{tmp_path / 'webhook.db'}"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(config, "head")
    eng = sa.create_engine(url)
    try:
        yield eng
    finally:
        eng.dispose()


def _insert(conn: sa.Connection, **overrides: object) -> None:
    values = {
        "id": uuid4().hex,
        "project_id": uuid4().hex,
        "url": "https://hook.example.com/notify",
        "secret_hash": "a" * 64,
        "secret_encrypted": "dGVzdA==",
        "event_types": "[]",
        "status": "ACTIVE",
        "consecutive_failures": 0,
        "created_at": datetime.now(UTC),
        "created_by": uuid4().hex,
        "updated_by": uuid4().hex,
    }
    values.update(overrides)
    conn.execute(
        sa.text(
            "INSERT INTO webhook_subscriptions "
            "(id, project_id, url, secret_hash, secret_encrypted, event_types, status,"
            " consecutive_failures, created_at, created_by, updated_by)"
            " VALUES (:id, :project_id, :url, :secret_hash, :secret_encrypted,"
            " :event_types, :status, :consecutive_failures, :created_at, :created_by,"
            " :updated_by)"
        ),
        values,
    )


def test_roundtrip_and_constraints(engine: sa.Engine, tmp_path: Path) -> None:
    inspector = sa.inspect(engine)
    assert "webhook_subscriptions" in inspector.get_table_names()

    with engine.begin() as conn:
        fixed_project = uuid4().hex
        _insert(conn, project_id=fixed_project)
        # 唯一约束 (project_id, url)：同项目同 URL 只允许一份
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, project_id=fixed_project, id=uuid4().hex)
        # 不同项目的同 URL 合法（复合唯一不跨项目）
        _insert(conn, project_id=uuid4().hex, id=uuid4().hex)
        # 状态 CHECK
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex, url="https://h.example.com/x", status="DELETED")
        # secret_hash 长度 CHECK
        with pytest.raises(sa.exc.IntegrityError):
            _insert(conn, id=uuid4().hex, url="https://h.example.com/y", secret_hash="short")

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{tmp_path / 'webhook.db'}")
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(config, "0084")
    assert "webhook_subscriptions" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "webhook_subscriptions" in sa.inspect(engine).get_table_names()
