"""阶段 0 骨架测试：健康检查、元信息、统一 request_id。"""

from __future__ import annotations

from app.core.config import get_settings
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
    # phase/modules 来自配置，必须反映真实阶段而不是硬编码 "0"。
    assert body["phase"] == get_settings().platform_phase
    assert body["phase"] != "0"
    assert "platform" in body["modules"]
    # 已实现模块必须出现在元信息里；数据平面在阶段 7 建。
    for module in ("devices", "collection", "datasets", "suppliers"):
        assert module in body["modules"]


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
