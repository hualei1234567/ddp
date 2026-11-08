"""安全响应头中间件测试。"""

from __future__ import annotations

import pytest
from app import main as main_module
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def _client(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> TestClient:
    settings = Settings(env="local", **overrides)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    return TestClient(create_app())


def test_baseline_security_headers_on_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch).get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_are_present_on_error_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(monkeypatch).get("/api/v1/not-exists")

    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"]


def test_hsts_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(
        monkeypatch,
        hsts_enabled=True,
        hsts_max_age_seconds=600,
        hsts_include_subdomains=True,
        hsts_preload=False,
    ).get("/health")

    assert response.headers["Strict-Transport-Security"] == "max-age=600; includeSubDomains"


def test_hsts_preload_value(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(
        monkeypatch,
        hsts_enabled=True,
        hsts_max_age_seconds=31_536_000,
        hsts_include_subdomains=True,
        hsts_preload=True,
    ).get("/health")

    assert (
        response.headers["Strict-Transport-Security"]
        == "max-age=31536000; includeSubDomains; preload"
    )


def test_csp_and_referrer_policy_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _client(
        monkeypatch,
        content_security_policy="default-src 'self'",
        referrer_policy="strict-origin-when-cross-origin",
    ).get("/health")

    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_docs_paths_skip_strict_csp(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch).get("/openapi.json")

    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_security_headers_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(monkeypatch, security_headers_enabled=False).get("/health")

    assert "X-Content-Type-Options" not in response.headers
    assert "Content-Security-Policy" not in response.headers
