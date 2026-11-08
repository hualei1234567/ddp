"""应用配置。

所有配置通过环境变量注入，前缀 ``DDP_``；本地开发从 ``.env`` 读取。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 密钥强度下限（字节级字符数）。低于该值视为配置不完整，非本地环境拒绝启动。
_MIN_JWT_SECRET_LENGTH = 32
# 明确视为未替换的占位密钥；命中后生产环境拒绝启动。
_PLACEHOLDER_JWT_SECRETS = frozenset(
    {
        "",
        "replace-me-in-production",
        "change-me",
        "changeme",
        "secret",
        "jwt-secret",
    }
)


class Settings(BaseSettings):
    """服务配置。字段名对应 ``DDP_<UPPER_NAME>``。"""

    model_config = SettingsConfigDict(
        env_prefix="DDP_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "production"
    app_name: str = "DDP"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    # 本地开发专用：仅当显式开启且请求来自 loopback 时，才接受 X-User-Id。
    # 生产环境禁止使用请求头身份；未接入真实认证时启动自检会拒绝启动。
    local_header_auth: bool = False

    database_url: str = "postgresql+psycopg://ddp:ddp@localhost:5432/ddp"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout_seconds: int = Field(default=5, ge=1)
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"  # noqa: S105 - 本地默认值，生产由环境注入
    s3_bucket: str = "ddp-raw"
    s3_region: str = "us-east-1"

    jwt_secret: str = "replace-me-in-production"  # noqa: S105 - 占位符，生产必须覆盖
    jwt_issuer: str = "ddp"
    jwt_audience: str = "ddp-api"
    jwt_expire_minutes: int = 720
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)

    @property
    def is_local(self) -> bool:
        return self.env.lower() in {"local", "dev", "development"}

    @property
    def has_complete_jwt_config(self) -> bool:
        return not self._jwt_config_problems()

    def validate_runtime_security(self) -> None:
        """启动自检：非本地环境必须配置完整的 JWT 认证，否则 fail-closed。"""
        if self.is_local:
            return
        if self.local_header_auth:
            raise RuntimeError(
                "非本地环境禁止启用本地 Header 认证（DDP_LOCAL_HEADER_AUTH），"
                "请改用 Bearer JWT。"
            )
        problems = self._jwt_config_problems()
        if problems:
            raise RuntimeError(
                "非本地环境 JWT 认证配置不完整，拒绝启动："
                + "；".join(problems)
                + "。请配置 DDP_JWT_SECRET（至少 "
                + str(_MIN_JWT_SECRET_LENGTH)
                + " 个字符的强随机值）、DDP_JWT_ISSUER、DDP_JWT_AUDIENCE。"
            )

    def _jwt_config_problems(self) -> list[str]:
        problems: list[str] = []
        secret = self.jwt_secret.strip()
        if secret.lower() in _PLACEHOLDER_JWT_SECRETS:
            problems.append("DDP_JWT_SECRET 为空或仍为占位符")
        elif len(secret) < _MIN_JWT_SECRET_LENGTH:
            problems.append(f"DDP_JWT_SECRET 长度不足 {_MIN_JWT_SECRET_LENGTH} 个字符")
        if secret != self.jwt_secret:
            problems.append("DDP_JWT_SECRET 不得包含首尾空白")
        if not self.jwt_issuer.strip():
            problems.append("DDP_JWT_ISSUER 为空")
        if not self.jwt_audience.strip():
            problems.append("DDP_JWT_AUDIENCE 为空")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
