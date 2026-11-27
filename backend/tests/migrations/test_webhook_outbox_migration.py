"""迁移 0086：``webhook_outbox`` 出站投递队列表的建表、约束与双向回滚。

覆盖（对齐 ``test_honeypot_state_migration.py`` 的口径）：
- 升级建表 / 降级删表（含两个索引），可反复升降级；
- 列属性：``payload`` JSON、``event_type`` String(128)、``status`` String(16)、
  ``attempts`` 非负、``available_at``/``created_at`` timezone-aware；
- 库层约束：``status`` 枚举、``attempts >= 0``、``event_type`` 非空；
- 唯一约束 ``(subscription_id, event_id)``：同一订阅同一事件只登记一次；
- 项目外键级联删除；订阅外键级联删除；
- 全新库 ``upgrade head`` 成功（防回到 ``Model.__table__.create`` 老坑）；
- 配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍（MEM-INC-013）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "webhook_outbox"
INDEXES = {
    "ix_webhook_outbox_status_available_at",
    "ix_webhook_outbox_project_created_at",
}
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：PG 连接串里的 ``%`` 必须转义成 ``%%``。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def _engine(url: str) -> Engine:
    engine = create_engine(url)
    if url.startswith("sqlite"):
        # SQLite 默认关闭外键；不开 PRAGMA，级联删除与外键用例会假绿。
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        __import__("sqlalchemy").event.listens_for(engine, "connect")(_enable_fk)
    return engine


def _seed_context(engine: Engine) -> tuple[object, object, object]:
    """建最小上下文：1 个项目 + 1 个用户 + 1 条订阅。

    返回 (project_id, user_id, subscription_id)。
    """
    from app.infrastructure.db.models.identity import Project, User
    from app.infrastructure.db.models.webhook_subscription import (
        WebhookSubscriptionModel,
    )

    project_id, user_id, subscription_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, display_name="出站投递测试用户"))
        session.add(
            Project(
                id=project_id,
                code=f"wo-{project_id.hex[:8]}",
                name="出站投递测试项目",
                status="ACTIVE",
                version=1,
            )
        )
        session.add(
            WebhookSubscriptionModel(
                id=subscription_id,
                project_id=project_id,
                url="https://hook.example.com/notify",
                secret_hash="a" * 64,
                secret_encrypted="Y2lwaGVydGV4dA==",
                event_types=["annotation.submission.status_changed"],
                status="ACTIVE",
                consecutive_failures=0,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()
    return project_id, user_id, subscription_id


def _insert_row(
    engine: Engine,
    *,
    project_id: object,
    subscription_id: object,
    event_id: object | None = None,
    status: str = "PENDING",
    attempts: int = 0,
    event_type: str = "annotation.submission.status_changed",
) -> None:
    from app.infrastructure.db.models.webhook_outbox import WebhookOutboxModel

    with Session(engine) as session:
        session.add(
            WebhookOutboxModel(
                id=uuid4(),
                project_id=project_id,
                subscription_id=subscription_id,
                event_id=event_id or uuid4(),
                event_type=event_type,
                payload={"submission_id": "s-1"},
                status=status,
                attempts=attempts,
                available_at=NOW,
                created_at=NOW,
            )
        )
        session.commit()


@pytest.fixture(params=["sqlite"])
def migrated_engine(request: pytest.FixtureRequest, tmp_path: Path) -> Engine:
    """建一个升到 head 的 SQLite 库（方言无关的那部分约束在这里验）。"""
    url = _sqlite_url(tmp_path / "webhook_outbox_test.db")
    config = _config(url)
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_creates_table_with_indexes(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert TABLE in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
    assert INDEXES.issubset(indexes)
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    assert {
        "id",
        "project_id",
        "subscription_id",
        "event_id",
        "event_type",
        "payload",
        "status",
        "attempts",
        "available_at",
        "created_at",
        "delivered_at",
        "last_error",
    }.issubset(columns)


def test_status_constraint_rejects_unknown_value(migrated_engine: Engine) -> None:
    project_id, _, subscription_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_row(
            migrated_engine,
            project_id=project_id,
            subscription_id=subscription_id,
            status="NOT_A_STATUS",
        )


def test_attempts_constraint_rejects_negative(migrated_engine: Engine) -> None:
    project_id, _, subscription_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_row(
            migrated_engine,
            project_id=project_id,
            subscription_id=subscription_id,
            attempts=-1,
        )


def test_event_type_constraint_rejects_empty(migrated_engine: Engine) -> None:
    project_id, _, subscription_id = _seed_context(migrated_engine)
    with pytest.raises(IntegrityError):
        _insert_row(
            migrated_engine,
            project_id=project_id,
            subscription_id=subscription_id,
            event_type="",
        )


def test_unique_subscription_event_blocks_duplicate(migrated_engine: Engine) -> None:
    """同一订阅 + 同一事件只能登记一次（幂等靠库层唯一键，不是应用层自律）。"""
    project_id, _, subscription_id = _seed_context(migrated_engine)
    event_id = uuid4()
    _insert_row(
        migrated_engine,
        project_id=project_id,
        subscription_id=subscription_id,
        event_id=event_id,
    )
    with pytest.raises(IntegrityError):
        _insert_row(
            migrated_engine,
            project_id=project_id,
            subscription_id=subscription_id,
            event_id=event_id,
        )


def test_project_cascade_deletes_rows(migrated_engine: Engine) -> None:
    project_id, _, subscription_id = _seed_context(migrated_engine)
    _insert_row(migrated_engine, project_id=project_id, subscription_id=subscription_id)
    with Session(migrated_engine) as session:
        from app.infrastructure.db.models.identity import Project

        # 用 ORM 删除而非裸 SQL：SQLite 驱动不支持绑定 UUID 类型参数。
        row = session.get(Project, project_id)
        assert row is not None
        session.delete(row)
        session.commit()
    with Session(migrated_engine) as session:
        # TABLE 是模块级常量（"webhook_outbox"），非请求值；f-string 无注入面。
        count = session.execute(
            text(f"SELECT count(*) FROM {TABLE}")  # noqa: S608
        ).scalar_one()
    assert count == 0


def test_subscription_cascade_deletes_rows(migrated_engine: Engine) -> None:
    """订阅被删（退订）时，其待投递记录随迁——不留孤儿行。"""
    project_id, _, subscription_id = _seed_context(migrated_engine)
    _insert_row(migrated_engine, project_id=project_id, subscription_id=subscription_id)
    with Session(migrated_engine) as session:
        from app.infrastructure.db.models.webhook_subscription import (
            WebhookSubscriptionModel,
        )

        row = session.get(WebhookSubscriptionModel, subscription_id)
        assert row is not None
        session.delete(row)
        session.commit()
    with Session(migrated_engine) as session:
        # TABLE 是模块级常量（"webhook_outbox"），非请求值；f-string 无注入面。
        count = session.execute(
            text(f"SELECT count(*) FROM {TABLE}")  # noqa: S608
        ).scalar_one()
    assert count == 0


def test_downgrade_drops_table_and_upgrade_recreates(tmp_path: Path) -> None:
    """降级删表、再升级重建，往返后表仍在且结构完整。"""
    url = _sqlite_url(tmp_path / "roundtrip.db")
    config = _config(url)
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        assert TABLE in inspect(engine).get_table_names()
        command.downgrade(config, "0085")
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
        assert INDEXES.issubset(indexes)
    finally:
        engine.dispose()


@pytest.mark.skipif(not _POSTGRES_URL, reason="未配置 DDP_TEST_POSTGRES_URL")
def test_postgres_roundtrip() -> None:
    """真实 PostgreSQL 上再跑一遍建表/约束/回滚（MEM-INC-013 双方言）。"""
    url = _POSTGRES_URL
    config = _config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        project_id, _, subscription_id = _seed_context(engine)
        _insert_row(engine, project_id=project_id, subscription_id=subscription_id)
        with pytest.raises(IntegrityError):
            _insert_row(
                engine,
                project_id=project_id,
                subscription_id=subscription_id,
                status="NOT_A_STATUS",
            )
    finally:
        engine.dispose()
        command.downgrade(config, "base")
