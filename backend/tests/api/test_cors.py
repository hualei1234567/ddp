"""CORS 白名单测试：默认拒绝、显式放行、精确匹配。"""

from __future__ import annotations

import pytest
from app import main as main_module
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from httpx import Response

_ALLOWED_ORIGIN = "https://console.example.com"
_DISALLOWED_ORIGIN = "https://evil.example.com"


def _client(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> TestClient:
    settings = Settings(env="local", **overrides)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return TestClient(create_app())


def _preflight(client: TestClient, origin: str) -> Response:
    return client.options(
        "/api/v1/platform/meta",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_empty_allowlist_rejects_cross_origin_simple_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch).get(
        "/health",
        headers={"Origin": _DISALLOWED_ORIGIN},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_empty_allowlist_rejects_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch).options(
        "/api/v1/platform/meta",
        headers={
            "Origin": _DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_explicit_allowlist_allows_matching_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, cors_allowed_origins=[_ALLOWED_ORIGIN])

    response = client.get("/health", headers={"Origin": _ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN


def test_explicit_allowlist_allows_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, cors_allowed_origins=[_ALLOWED_ORIGIN])

    response = _preflight(client, _ALLOWED_ORIGIN)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN


def test_allowlist_is_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, cors_allowed_origins=[_ALLOWED_ORIGIN])

    response = client.get(
        "/health",
        headers={"Origin": f"{_ALLOWED_ORIGIN}.evil.example.com"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_credentials_are_only_echoed_for_allowlisted_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch,
        cors_allowed_origins=[_ALLOWED_ORIGIN],
        cors_allow_credentials=True,
    )

    allowed = client.get("/health", headers={"Origin": _ALLOWED_ORIGIN})
    denied = client.get("/health", headers={"Origin": _DISALLOWED_ORIGIN})

    assert allowed.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers
