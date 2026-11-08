"""应用配置。

所有配置通过环境变量注入，前缀 ``DDP_``；本地开发从 ``.env`` 读取。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    jwt_expire_minutes: int = 720

    @property
    def is_local(self) -> bool:
        return self.env.lower() in {"local", "dev", "development"}

    def validate_runtime_security(self) -> None:
        """启动自检：非本地环境没有真实认证时 fail-closed。

        当前代码只实现了本地请求头身份适配器，尚无 JWT/OIDC 校验实现。
        因此非本地环境一律拒绝启动；接入真实认证后，应在此处改为检查
        对应认证配置是否完整，而不是默认放行。
        """
        if self.is_local:
            return
        raise RuntimeError(
            "非本地环境未接入真实认证（JWT/OIDC），拒绝启动；"
            "本地开发请设置 DDP_ENV=local，并按需显式开启 "
            "DDP_LOCAL_HEADER_AUTH=true（仅 loopback 生效）。"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
