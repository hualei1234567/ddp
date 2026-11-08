"""安全配置测试：默认生产环境、本地 Header 开关与启动自检。"""

from __future__ import annotations

import pytest
from app import main as main_module
from app.core.config import Settings
from app.main import create_app


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDP_ENV", raising=False)
    monkeypatch.delenv("DDP_LOCAL_HEADER_AUTH", raising=False)


def test_env_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    settings = Settings()

    assert settings.env == "production"
    assert settings.is_local is False


def test_local_header_auth_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    assert Settings(env="local").local_header_auth is False


def test_non_local_startup_requires_real_auth() -> None:
    with pytest.raises(RuntimeError, match="真实认证"):
        Settings(env="production").validate_runtime_security()


def test_non_local_startup_still_rejects_local_header_auth() -> None:
    """即使误开本地 Header 开关，也不能让非本地环境启动。"""
    settings = Settings(env="production", local_header_auth=True)

    with pytest.raises(RuntimeError, match="真实认证"):
        settings.validate_runtime_security()


def test_local_startup_is_allowed_even_with_header_auth_disabled() -> None:
    Settings(env="local", local_header_auth=False).validate_runtime_security()


def test_create_app_fails_closed_when_auth_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(env="production"),
    )

    with pytest.raises(RuntimeError, match="真实认证"):
        create_app()
