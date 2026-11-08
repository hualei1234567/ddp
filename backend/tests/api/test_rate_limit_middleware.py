"""限流中间件测试：认证桶、写桶、关闭开关与代理头信任边界。"""

from __future__ import annotations

import pytest
from app import main as main_module
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def _client(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> TestClient:
    values: dict[str, object] = {
        "rate_limit_auth_requests": 2,
        "rate_limit_auth_window_seconds": 60,
        "rate_limit_write_requests": 2,
        "rate_limit_write_window_seconds": 60,
    }
    values.update(overrides)
    settings = Settings(env="local", **values)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    app = create_app()

    @app.api_route("/api/v1/auth/ping", methods=["GET", "POST"])
    def auth_ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/test/write")
    def write_probe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/test/read")
    def read_probe() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_auth_path_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)

    assert client.get("/api/v1/auth/ping").status_code == 200
    assert client.get("/api/v1/auth/ping").status_code == 200
    limited = client.get("/api/v1/auth/ping")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.headers["X-RateLimit-Limit"] == "2"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert limited.headers["X-Request-Id"]
    # 限流响应同样必须经过安全头中间件（验证中间件顺序）。
    assert limited.headers["X-Content-Type-Options"] == "nosniff"


def test_write_methods_are_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)

    assert client.post("/api/v1/test/write").status_code == 200
    assert client.post("/api/v1/test/write").status_code == 200
    limited = client.post("/api/v1/test/write")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"


def test_read_requests_are_not_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, rate_limit_write_requests=1)

    for _ in range(5):
        assert client.get("/api/v1/test/read").status_code == 200


def test_rate_limiting_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_enabled=False,
        rate_limit_auth_requests=1,
    )

    for _ in range(5):
        assert client.get("/api/v1/auth/ping").status_code == 200


def test_auth_bucket_takes_precedence_over_write_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_write_requests=100,
    )

    assert client.post("/api/v1/auth/ping").status_code == 200
    assert client.post("/api/v1/auth/ping").status_code == 429


def test_auth_path_prefix_respects_segment_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/api/v1/authz`` 不得命中 ``/api/v1/auth`` 认证桶。"""
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_write_requests=100,
    )

    assert client.get("/api/v1/authz/ping").status_code == 404
    assert client.get("/api/v1/authz/ping").status_code == 404


def test_forwarded_for_is_not_trusted_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, rate_limit_auth_requests=1)

    first = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )
    second = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.2"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_forwarded_for_can_be_trusted_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        rate_limit_auth_requests=1,
        rate_limit_trust_forwarded_for=True,
    )

    first = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )
    other = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.2"},
    )
    repeated = client.get(
        "/api/v1/auth/ping",
        headers={"X-Forwarded-For": "203.0.113.1"},
    )

    assert first.status_code == 200
    assert other.status_code == 200
    assert repeated.status_code == 429
