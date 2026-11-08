"""阶段 0 骨架测试：健康检查、元信息、统一 request_id。"""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "DDP"


def test_platform_meta() -> None:
    response = client.get("/api/v1/platform/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "0"
    assert "platform" in body["modules"]


def test_request_id_is_echoed() -> None:
    response = client.get("/health", headers={"X-Request-Id": "req-test-1"})
    assert response.headers["X-Request-Id"] == "req-test-1"


def test_request_id_is_generated_when_missing() -> None:
    response = client.get("/health")
    assert response.headers.get("X-Request-Id")


def test_unknown_route_returns_json_404() -> None:
    response = client.get("/api/v1/not-exists")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
