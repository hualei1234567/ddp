"""Webhook 订阅 API 测试（阶段 5）。

覆盖：创建（secret 仅一次）、重复 URL 409、列表、删除、权限矩阵
（owner 可、editor 403、viewer 403、外部人 404）、URL 安全校验。
"""

from __future__ import annotations

from uuid import UUID

from app.core.config import Settings
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

# 创建订阅需要加密密钥（用时校验）；测试显式注入，模拟生产配置
_WEBHOOK_KEY = "test-webhook-encryption-key-32bytes!!"


def _client(factory: sessionmaker[Session], user_id: UUID):
    """带 webhook 密钥配置的测试客户端。"""
    settings = Settings(
        webhook_encryption_key=_WEBHOOK_KEY,
        database_url="sqlite+pysqlite:///:memory:",
    )
    return make_client(factory, user_id, settings_override=settings)


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/webhook-subscriptions"


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "url": "https://hook.example.com/notify",
        "secret": "example-signing-key-0123456789",
        "event_types": ["annotation.completed", "annotation.honeypot.paused"],
    }
    payload.update(overrides)
    return payload


def test_owner_creates_lists_and_deletes(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)

    created = owner.post(_url(ids.project_a), json=_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["url"] == "https://hook.example.com/notify"
    assert body["status"] == "ACTIVE"
    assert body["consecutive_failures"] == 0
    # secret 明文只在创建响应里出现一次
    assert body["secret"] == "example-signing-key-0123456789"

    listed = owner.get(_url(ids.project_a))
    assert listed.status_code == 200
    page = listed.json()
    assert "items" in page  # 响应为分页结构（与契约一致）
    items = page["items"]
    assert len(items) == 1
    # 列表视图**不得**暴露 secret（明文或 hash）
    assert "secret" not in items[0]
    assert "secret_hash" not in items[0]

    deleted = owner.delete(_url(ids.project_a) + f"/{body['id']}")
    assert deleted.status_code == 204
    assert owner.get(_url(ids.project_a)).json()["items"] == []


def test_duplicate_url_returns_409(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)

    assert owner.post(_url(ids.project_a), json=_payload()).status_code == 201
    again = owner.post(_url(ids.project_a), json=_payload(secret="another-example-secret"))
    assert again.status_code == 409


def test_external_http_url_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)

    response = owner.post(_url(ids.project_a), json=_payload(url="http://evil.example.com/hook"))
    assert response.status_code == 422


def test_empty_event_types_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)

    response = owner.post(_url(ids.project_a), json=_payload(event_types=[]))
    assert response.status_code == 422


def test_editor_and_viewer_cannot_manage(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)
    editor = _client(factory, ids.editor_a)
    viewer = _client(factory, ids.viewer_a)

    created = owner.post(_url(ids.project_a), json=_payload()).json()

    assert editor.post(_url(ids.project_a), json=_payload()).status_code == 403
    assert editor.get(_url(ids.project_a)).status_code == 403
    assert editor.delete(_url(ids.project_a) + f"/{created['id']}").status_code == 403
    assert viewer.get(_url(ids.project_a)).status_code == 403


def test_outsider_gets_404_or_403(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    outsider = _client(factory, ids.outsider)

    response = outsider.get(_url(ids.project_a))
    assert response.status_code in {403, 404}


def test_project_isolation(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner_a = _client(factory, ids.owner_a)

    created = owner_a.post(_url(ids.project_a), json=_payload()).json()
    # owner_a 也属于 project_b，但 project_b 的列表应独立（订阅是项目资产）
    other = owner_a.get(_url(ids.project_b))
    assert other.status_code == 200
    assert other.json()["items"] == []

    # 删除 project_a 的订阅不影响 project_b
    owner_a.delete(_url(ids.project_a) + f"/{created['id']}")
    assert owner_a.get(_url(ids.project_b)).json()["items"] == []


def test_delete_unknown_returns_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    owner = _client(factory, ids.owner_a)

    response = owner.delete(_url(ids.project_a) + f"/{UUID(int=0).hex}")
    assert response.status_code == 404
