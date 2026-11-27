"""Webhook 对外出站端到端链路测试（阶段 6）。

验证完整链路：**业务事件 → 登记到出站队列 → Worker 真实 HTTP 投递 → 下游收到
带签名的请求**。

与既有 ``test_webhook_subscription_api.py``（订阅 CRUD）的区别：那个只验
"订阅能建能删"，这个验"事件真的送出去了"——阶段 5 的能力缺口正是在这里
（投递用例没有任何生产调用点）。

覆盖：
- 开关关闭时业务事件**不产生**任何待投递记录（零副作用）；
- 开关开启时业务事件登记到 ``webhook_outbox``；
- Worker 用真实 HTTP（本地回环接收服务）投递，下游能收到并**验签通过**；
- 白名单外的事件类型不登记。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import pytest
from app.core.config import Settings
from app.infrastructure.db.models.webhook_outbox import WebhookOutboxModel
from app.workers.webhook_outbound import WebhookOutboundWorker
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.api import annotation_support
from tests.api.conftest import ApiIds, make_client

_WEBHOOK_KEY = "test-webhook-encryption-key-32bytes!!"
_PLAIN_SECRET = "example-signing-key-0123456789"
_EVENT_TYPE = "annotation.submission.status_changed"


class _Receiver:
    """本地回环 HTTP 接收服务：记录收到的请求，供断言与验签。"""

    def __init__(self) -> None:
        self.received: list[dict[str, object]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                receiver.received.append(
                    {
                        "path": self.path,
                        # 键统一小写：HTTP header 名大小写不敏感，httpx 发出的是
                        # 小写形式；用原始大小写做字典索引会漏掉签名头。
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                """静默：避免测试输出被访问日志刷屏。"""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/hook"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture()
def receiver() -> Iterator[_Receiver]:
    instance = _Receiver()
    try:
        yield instance
    finally:
        instance.stop()


def _settings(*, outbound_enabled: bool) -> Settings:
    return Settings(
        env="local",
        webhook_encryption_key=_WEBHOOK_KEY,
        webhook_outbound_enabled=outbound_enabled,
        # 本地例外：允许投到回环地址的接收服务。
        # 生产必须为 false——守卫默认拒绝环回/私网/云元数据地址，
        # 这是防 SSRF 的正确默认，不能为了测试方便在生产放宽。
        webhook_outbound_allow_private=True,
        database_url="sqlite+pysqlite:///:memory:",
    )


def _client(factory: sessionmaker[Session], user_id: UUID, *, enabled: bool) -> TestClient:
    return make_client(factory, user_id, settings_override=_settings(outbound_enabled=enabled))


def _create_subscription(owner: TestClient, project_id: UUID, url: str) -> dict[str, object]:
    response = owner.post(
        f"/api/v1/projects/{project_id}/webhook-subscriptions",
        json={
            "url": url,
            "secret": _PLAIN_SECRET,
            "event_types": [_EVENT_TYPE],
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _pending_rows(factory: sessionmaker[Session]) -> list[WebhookOutboxModel]:
    with factory() as session:
        return list(session.scalars(select(WebhookOutboxModel)))


def _trigger_submission_event(
    editor: TestClient,
    owner: TestClient,
    factory: sessionmaker[Session],
    ids: ApiIds,
) -> None:
    """走真实 API 触发一次标注提交（产生 ``annotation.submission.status_changed``）。

    用真实业务链路而不是直接写 outbox：只有真实链路才能证明"登记点接对了"。
    """
    from tests.api.annotation_support import (
        create_published_schema,
        create_ready_work,
        create_snapshot_set,
        create_submitted,
        seed_episode,
    )

    episode_id = seed_episode(factory, ids.project_a, ids.editor_a, "episode-wh")
    schema = create_published_schema(editor, owner, ids.project_a, "schema-wh")
    sample_set = create_snapshot_set(editor, ids.project_a, "sample-wh")
    work = create_ready_work(
        editor,
        owner,
        ids.project_a,
        schema,
        sample_set,
        episode_id,
        ids.editor_a,
        "task-wh",
    )
    create_submitted(
        editor,
        ids.project_a,
        work["assignment"]["assignment_id"],
        episode_id,
    )


def test_outbound_disabled_registers_nothing(
    api_db: tuple[sessionmaker[Session], ApiIds], receiver: _Receiver
) -> None:
    """开关关闭：业务事件不产生任何待投递记录（零副作用）。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, enabled=False)
    owner = _client(factory, ids.owner_a, enabled=False)

    url = receiver.start()
    _create_subscription(owner, ids.project_a, url)
    _trigger_submission_event(editor, owner, factory, ids)

    assert _pending_rows(factory) == []
    assert receiver.received == []


def test_outbound_enabled_registers_pending_row(
    api_db: tuple[sessionmaker[Session], ApiIds], receiver: _Receiver
) -> None:
    """开关开启：业务事件登记到出站队列。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, enabled=True)
    owner = _client(factory, ids.owner_a, enabled=True)

    url = receiver.start()
    _create_subscription(owner, ids.project_a, url)
    _trigger_submission_event(editor, owner, factory, ids)

    rows = _pending_rows(factory)
    # 创建提交（CREATED）与提交动作（SUBMITTED）是**两个不同的业务事件**，
    # 各自登记一条待投递——下游按事件 id 去重，不会收到同一事件的两次。
    assert len(rows) == 2
    assert all(row.status == "PENDING" for row in rows)
    assert all(row.event_type == _EVENT_TYPE for row in rows)
    assert len({row.event_id for row in rows}) == 2


def test_worker_delivers_to_real_http_receiver(
    api_db: tuple[sessionmaker[Session], ApiIds], receiver: _Receiver
) -> None:
    """端到端：Worker 真实 POST 到下游，下游收到且**验签通过**。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, enabled=True)
    owner = _client(factory, ids.owner_a, enabled=True)

    url = receiver.start()
    _create_subscription(owner, ids.project_a, url)
    _trigger_submission_event(editor, owner, factory, ids)

    worker = WebhookOutboundWorker(factory, _settings(outbound_enabled=True))
    result = worker.process_once()

    assert result.status == "delivered"
    assert len(receiver.received) == 1
    request = receiver.received[0]
    assert request["path"] == "/hook"

    headers = request["headers"]
    assert isinstance(headers, dict)
    signature = headers["x-ddp-signature"]
    timestamp = headers["x-ddp-timestamp"]
    body = request["body"]
    assert isinstance(body, bytes)

    # 下游按与平台一致的算法验签：HMAC-SHA256(secret, timestamp + "\n" + body)
    expected = (
        "sha256="
        + hmac.new(
            _PLAIN_SECRET.encode("utf-8"),
            f"{timestamp}\n".encode() + body,
            hashlib.sha256,
        ).hexdigest()
    )
    assert signature == expected

    payload = json.loads(body.decode("utf-8"))
    # 投递体是信封：下游能从请求体本身知道是哪个事件、哪个事件 id。
    assert payload["event_type"] == _EVENT_TYPE
    assert payload["event_id"]
    assert payload["project_id"] == str(ids.project_a)
    assert payload["occurred_at"]
    assert "submission_id" in payload["data"]

    rows = _pending_rows(factory)
    assert rows[0].status == "DELIVERED"


def test_worker_failure_keeps_row_pending(
    api_db: tuple[sessionmaker[Session], ApiIds], receiver: _Receiver
) -> None:
    """下游不可达 → 记录留在 PENDING 等重试，绝不标成功。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, enabled=True)
    owner = _client(factory, ids.owner_a, enabled=True)

    url = receiver.start()
    _create_subscription(owner, ids.project_a, url)
    _trigger_submission_event(editor, owner, factory, ids)
    receiver.stop()  # 下游下线 → 投递必然失败

    worker = WebhookOutboundWorker(factory, _settings(outbound_enabled=True))
    result = worker.process_once()

    assert result.status in {"retry", "dead_letter"}
    rows = _pending_rows(factory)
    assert rows[0].status != "DELIVERED"


def test_whitelist_excludes_sensitive_events(
    api_db: tuple[sessionmaker[Session], ApiIds], receiver: _Receiver
) -> None:
    """订阅了敏感事件类型也不登记（白名单在登记器一处把关）。"""
    factory, ids = api_db
    editor = _client(factory, ids.editor_a, enabled=True)
    owner = _client(factory, ids.owner_a, enabled=True)

    url = receiver.start()
    response = owner.post(
        f"/api/v1/projects/{ids.project_a}/webhook-subscriptions",
        json={
            "url": url,
            "secret": _PLAIN_SECRET,
            "event_types": ["annotation.gold_set.answers_accessed"],
        },
    )
    assert response.status_code == 201, response.text

    _trigger_submission_event(editor, owner, factory, ids)

    assert _pending_rows(factory) == []


__all__ = ["annotation_support"]
