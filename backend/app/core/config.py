"""应用配置。

所有配置通过环境变量注入，前缀 ``DDP_``；本地开发从 ``.env`` 读取。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务配置。字段名对应 ``DDP_<UPPER_NAME>``。"""

    model_config = SettingsConfigDict(
        env_prefix="DDP_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    app_name: str = "DDP"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://ddp:ddp@localhost:5432/ddp"
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
