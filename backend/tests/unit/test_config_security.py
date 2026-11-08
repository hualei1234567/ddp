"""安全配置测试：默认生产 fail-closed、JWT 配置完整性、本地 Header 开关。"""

from __future__ import annotations

import pytest
from app import main as main_module
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from tests.jwt_support import JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DDP_ENV",
        "DDP_LOCAL_HEADER_AUTH",
        "DDP_JWT_SECRET",
        "DDP_JWT_ISSUER",
        "DDP_JWT_AUDIENCE",
    ):
        monkeypatch.delenv(name, raising=False)


def _jwt_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "staging",
        "jwt_secret": JWT_SECRET,
        "jwt_issuer": JWT_ISSUER,
        "jwt_audience": JWT_AUDIENCE,
    }
    values.update(overrides)
    return Settings(**values)


def test_env_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    settings = Settings()

    assert settings.env == "production"
    assert settings.is_local is False


def test_local_header_auth_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    assert Settings(env="local").local_header_auth is False


def test_non_local_startup_requires_jwt_config() -> None:
    with pytest.raises(RuntimeError, match="JWT"):
        Settings(env="production").validate_runtime_security()


def test_non_local_startup_rejects_placeholder_secret() -> None:
    settings = _jwt_settings(jwt_secret="replace-me-in-production")  # noqa: S106 - 占位符用例

    with pytest.raises(RuntimeError, match="占位符"):
        settings.validate_runtime_security()


def test_non_local_startup_rejects_short_secret() -> None:
    settings = _jwt_settings(jwt_secret="too-short")  # noqa: S106 - 弱密钥用例

    with pytest.raises(RuntimeError, match="长度不足"):
        settings.validate_runtime_security()


def test_non_local_startup_rejects_secret_with_surrounding_whitespace() -> None:
    settings = _jwt_settings(jwt_secret=f" {JWT_SECRET} ")  # noqa: S106 - 空白配置用例

    with pytest.raises(RuntimeError, match="首尾空白"):
        settings.validate_runtime_security()


def test_non_local_startup_rejects_empty_issuer_and_audience() -> None:
    settings = _jwt_settings(jwt_issuer=" ", jwt_audience="")

    with pytest.raises(RuntimeError, match="ISSUER") as error:
        settings.validate_runtime_security()
    assert "AUDIENCE" in str(error.value)


def test_non_local_startup_accepts_complete_jwt_config() -> None:
    settings = _jwt_settings()

    settings.validate_runtime_security()

    assert settings.has_complete_jwt_config is True


def test_non_local_startup_still_rejects_local_header_auth() -> None:
    """即使 JWT 配置完整，非本地环境也不得启用 Header 后门。"""
    settings = _jwt_settings(local_header_auth=True)

    with pytest.raises(RuntimeError, match="Header"):
        settings.validate_runtime_security()


def test_local_startup_is_allowed_even_with_header_auth_disabled() -> None:
    Settings(env="local", local_header_auth=False).validate_runtime_security()


def test_create_app_fails_closed_when_jwt_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(env="production"),
    )

    with pytest.raises(RuntimeError, match="JWT"):
        create_app()


def test_create_app_starts_and_health_is_ok_with_staging_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟 staging 重建：JWT 配置完整时应用可启动，/health 返回 200。"""
    monkeypatch.setattr(main_module, "get_settings", lambda: _jwt_settings())

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
