"""Webhook 出站投递 Worker 测试（阶段 6）。

覆盖：
- 无待投递记录时返回 idle；
- 下游返回 2xx → 记录标记为 DELIVERED；
- 下游失败 → 退避重试（记录留在 PENDING、attempts 递增、可见时间推后）；
- 连续失败达上限 → 标记死信（FAILED），不再自动投递；
- **绝不伪造成功**：下游返回非 2xx 时绝不标 DELIVERED；
- 投递确实带上了 HMAC 签名头（下游可验签）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.webhook_outbox import WebhookOutboxModel
from app.infrastructure.repositories.webhook_outbox import SqlWebhookOutboxRepository
from app.workers.webhook_outbound import WebhookOutboundWorker
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
KEY = "test-encryption-key-32-bytes-long-ok"
#: 订阅明文 secret（只在本测试内出现；入库前用同一把密钥加密）。
_PLAIN_SECRET = "example-webhook-hmac-secret-0123456789"


def _engine(tmp_path: Path) -> object:
    """用 Alembic 升级建库（与仓库测试口径一致：不用 create_all 造结构）。"""
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "webhook_worker_test.db"
    url = f"sqlite+pysqlite:///{db_path}"
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("prepend_sys_path", str(backend_dir))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")

    engine = create_engine(url)

    def _enable_fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listens_for(engine, "connect")(_enable_fk)
    return engine


def _seed(engine: object) -> tuple[object, object, object]:
    """建最小上下文：项目 + 用户 + 订阅。"""
    from app.domain.webhook.subscription import encrypt_secret, hash_secret
    from app.infrastructure.db.models.identity import Project, User
    from app.infrastructure.db.models.webhook_subscription import (
        WebhookSubscriptionModel,
    )

    project_id, user_id, subscription_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:  # type: ignore[arg-type]
        session.add(User(id=user_id, display_name="出站 Worker 测试用户"))
        session.add(
            Project(
                id=project_id,
                code=f"wow-{project_id.hex[:8]}",
                name="出站 Worker 测试项目",
                status="ACTIVE",
                version=1,
            )
        )
        session.add(
            WebhookSubscriptionModel(
                id=subscription_id,
                project_id=project_id,
                url="https://hook.example.com/notify",
                secret_hash=hash_secret(_PLAIN_SECRET),
                # 密文必须用与 Worker 相同的密钥真实加密：写假密文会在
                # 解密时抛 UnicodeDecodeError，把"配置错误"伪装成"投递失败"。
                secret_encrypted=encrypt_secret(_PLAIN_SECRET, KEY.encode("utf-8")),
                event_types=["annotation.submission.status_changed"],
                status="ACTIVE",
                consecutive_failures=0,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()
    return project_id, user_id, subscription_id


def _enqueue(
    engine: object,
    *,
    project_id: object,
    subscription_id: object,
    attempts: int = 0,
    available_at: datetime = NOW,
) -> object:
    event_id = uuid4()
    with Session(engine) as session:  # type: ignore[arg-type]
        SqlWebhookOutboxRepository(session).enqueue(
            project_id=project_id,  # type: ignore[arg-type]
            subscription_id=subscription_id,  # type: ignore[arg-type]
            event_id=event_id,
            event_type="annotation.submission.status_changed",
            payload={"submission_id": "s-1"},
            now=NOW,
        )
        session.commit()
        row = session.scalar(
            select(WebhookOutboxModel).where(WebhookOutboxModel.event_id == event_id)
        )
        assert row is not None
        row.attempts = attempts
        row.available_at = available_at
        session.commit()
        return row.id


def _row(engine: object, delivery_id: object) -> tuple[str, int, str | None]:
    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.get(WebhookOutboxModel, delivery_id)
        assert row is not None
        return row.status, row.attempts, row.last_error


class FakeTransport:
    """记录投递请求，返回配置好的状态码。"""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> int:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.status


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "env": "local",
        "webhook_outbound_enabled": True,
        "webhook_encryption_key": KEY,
        "webhook_outbound_max_attempts": 3,
        "webhook_outbound_retry_base_seconds": 5.0,
        "webhook_outbound_retry_max_seconds": 300.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def context(tmp_path: Path) -> tuple[object, object, object, sessionmaker]:
    engine = _engine(tmp_path)
    project_id, _, subscription_id = _seed(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, project_id, subscription_id, factory


def test_idle_when_no_pending_record(context: tuple) -> None:
    _, _, _, factory = context
    worker = WebhookOutboundWorker(factory, _settings(), transport=FakeTransport(200))

    result = worker.process_once(now=NOW)

    assert result.status == "idle"


def test_successful_delivery_marks_delivered(context: tuple) -> None:
    engine, project_id, subscription_id, factory = context
    delivery_id = _enqueue(engine, project_id=project_id, subscription_id=subscription_id)
    transport = FakeTransport(200)
    worker = WebhookOutboundWorker(factory, _settings(), transport=transport)

    result = worker.process_once(now=NOW)

    assert result.status == "delivered"
    status, attempts, _ = _row(engine, delivery_id)
    assert status == "DELIVERED"
    assert attempts == 0


def test_delivery_carries_signature_headers(context: tuple) -> None:
    """投递必须带 HMAC 签名头，下游才能验签（不是裸 POST）。"""
    engine, project_id, subscription_id, factory = context
    _enqueue(engine, project_id=project_id, subscription_id=subscription_id)
    transport = FakeTransport(200)
    worker = WebhookOutboundWorker(factory, _settings(), transport=transport)

    worker.process_once(now=NOW)

    assert len(transport.calls) == 1
    headers = transport.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["X-DDP-Signature"].startswith("sha256=")
    assert headers["X-DDP-Timestamp"] == str(int(NOW.timestamp()))
    assert headers["Content-Type"] == "application/json"


def test_failed_delivery_retries_with_backoff(context: tuple) -> None:
    engine, project_id, subscription_id, factory = context
    delivery_id = _enqueue(engine, project_id=project_id, subscription_id=subscription_id)
    worker = WebhookOutboundWorker(factory, _settings(), transport=FakeTransport(500))

    result = worker.process_once(now=NOW)

    assert result.status == "retry"
    status, attempts, last_error = _row(engine, delivery_id)
    # 未达上限 → 留在 PENDING 等重试，不进死信
    assert status == "PENDING"
    assert attempts == 1
    assert last_error is not None


def test_failed_delivery_advances_available_at(context: tuple) -> None:
    """重试必须把可见时间推后，否则会立刻再打挂掉的下游。"""
    engine, project_id, subscription_id, factory = context
    delivery_id = _enqueue(engine, project_id=project_id, subscription_id=subscription_id)
    worker = WebhookOutboundWorker(factory, _settings(), transport=FakeTransport(500))

    worker.process_once(now=NOW)

    with Session(engine) as session:  # type: ignore[arg-type]
        row = session.get(WebhookOutboxModel, delivery_id)
        assert row is not None
        # SQLite 不保存时区，读回是 naive：统一按 UTC 比较再比大小。
        available = row.available_at
        if available.tzinfo is None:
            available = available.replace(tzinfo=UTC)
        assert available > NOW


def test_delivery_never_marks_success_on_non_2xx(context: tuple) -> None:
    """绝不伪造成功：下游非 2xx 绝不标 DELIVERED。"""
    engine, project_id, subscription_id, factory = context
    delivery_id = _enqueue(engine, project_id=project_id, subscription_id=subscription_id)
    worker = WebhookOutboundWorker(factory, _settings(), transport=FakeTransport(503))

    worker.process_once(now=NOW)

    status, _, _ = _row(engine, delivery_id)
    assert status != "DELIVERED"


def test_dead_letter_after_max_attempts(context: tuple) -> None:
    """达重试上限 → 死信（FAILED），不再自动投递。"""
    engine, project_id, subscription_id, factory = context
    # 预置 attempts=2，max_attempts=3 → 本次失败即达上限
    delivery_id = _enqueue(
        engine, project_id=project_id, subscription_id=subscription_id, attempts=2
    )
    worker = WebhookOutboundWorker(factory, _settings(), transport=FakeTransport(500))

    result = worker.process_once(now=NOW)

    assert result.status == "dead_letter"
    status, attempts, _ = _row(engine, delivery_id)
    assert status == "FAILED"
    assert attempts == 3


def test_worker_main_refuses_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭时 Worker 拒绝启动（避免"以为在投递"）。"""
    from app.workers import webhook_outbound

    monkeypatch.setenv("DDP_WEBHOOK_OUTBOUND_ENABLED", "false")
    monkeypatch.setenv("DDP_ENV", "local")
    monkeypatch.setenv("DDP_WEBHOOK_ENCRYPTION_KEY", KEY)
    monkeypatch.setattr(
        webhook_outbound, "get_settings", lambda: _settings(webhook_outbound_enabled=False)
    )

    assert webhook_outbound.main([]) == 2
