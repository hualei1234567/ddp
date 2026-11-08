"""应用配置。

所有配置通过环境变量注入，前缀 ``DDP_``；本地开发从 ``.env`` 读取。
列表类型（CORS 来源、限流路径前缀等）通过 JSON 数组注入，例如::

    DDP_CORS_ALLOWED_ORIGINS=["https://console.example.com"]
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

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
# MinIO / S3 的默认或占位凭据；非本地环境命中后拒绝启动。
_PLACEHOLDER_S3_CREDENTIALS = frozenset(
    {
        "",
        "minioadmin",
        "changeme",
        "change-me",
        "change_me",
        "replace-me-in-production",
        "secret",
        "access-key",
        "secret-key",
        "admin",
    }
)
# API 响应默认 CSP：不加载任何外部资源，禁止被嵌入。
_DEFAULT_CSP = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
# HSTS preload 要求的最短 max-age（一年）。
_HSTS_PRELOAD_MIN_AGE_SECONDS = 31_536_000


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
    s3_secret_key: str = "minioadmin"  # noqa: S105 - 本地默认值，生产必须覆盖
    s3_bucket: str = "ddp-raw"
    s3_region: str = "us-east-1"

    # 上传策略：由项目/部署配置覆盖，不在用例内硬编码阈值。
    upload_max_size_bytes: int = Field(default=10 * 1024**3, gt=0)
    upload_part_size_bytes: int = Field(default=16 * 1024**2, gt=0)
    upload_session_ttl_hours: int = Field(default=24, gt=0)
    upload_url_ttl_seconds: int = Field(default=900, gt=0, le=3600)
    # 下载预签名 URL 必须短时有效；默认 5 分钟，上限 1 小时。
    download_url_ttl_seconds: int = Field(default=300, gt=0, le=3600)
    # 空列表表示使用契约默认白名单（由应用层注入），避免配置与契约重复维护。
    upload_allowed_content_types: list[str] = Field(default_factory=list)

    # 标注分配容量上限；由部署配置注入，HTTP 层统一执行。
    annotation_max_open_items: int = Field(default=50, gt=0)
    annotation_max_planned_minutes: int = Field(default=2400, gt=0)

    # RawAsset 异步校验 Worker。所有阈值由部署配置注入，不在用例内硬编码。
    validation_worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    validation_stream_chunk_size_bytes: int = Field(default=1024 * 1024, gt=0)
    validation_max_attempts: int = Field(default=5, ge=1, le=100)
    validation_retry_base_seconds: float = Field(default=5.0, gt=0)
    validation_retry_max_seconds: float = Field(default=300.0, gt=0)

    # ExportJob 异步导出 Worker 与产物策略。
    export_storage_prefix: str = "exports"
    export_default_timeout_seconds: int = Field(default=3600, ge=1)
    export_max_timeout_seconds: int = Field(default=86400, ge=1)
    export_default_max_attempts: int = Field(default=3, ge=1)
    export_max_attempts_limit: int = Field(default=20, ge=1)
    export_artifact_chunk_size_bytes: int = Field(default=1024 * 1024, gt=0)
    export_worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    export_worker_max_attempts: int = Field(default=5, ge=1, le=100)
    export_worker_retry_base_seconds: float = Field(default=5.0, gt=0)
    export_worker_retry_max_seconds: float = Field(default=300.0, gt=0)

    # 训练平台 Adapter：回调密钥与能力声明只来自配置/Secret 管理，不硬编码。
    # DDP_TRAINING_ADAPTER_SHARED_SECRETS 形如 {"mock": "<32+ 字符密钥>"}。
    training_adapter_shared_secrets: dict[str, str] = Field(default_factory=dict)
    training_adapter_capabilities: dict[str, list[str]] = Field(default_factory=dict)
    training_callback_time_window_seconds: int = Field(default=300, ge=1, le=3600)
    training_callback_nonce_ttl_seconds: int = Field(default=600, ge=1)

    jwt_secret: str = "replace-me-in-production"  # noqa: S105 - 占位符，生产必须覆盖
    jwt_issuer: str = "ddp"
    jwt_audience: str = "ddp-api"
    jwt_expire_minutes: int = 720
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)

    # 安全响应头。CSP 仅作用于 API/健康检查；FastAPI 文档页面依赖 CDN，
    # 生产环境应通过反向代理关闭或单独定制文档页。
    security_headers_enabled: bool = True
    content_security_policy: str = _DEFAULT_CSP
    referrer_policy: str = "no-referrer"
    # HSTS 仅应在 HTTPS（含反向代理终止 TLS）场景显式开启。
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = Field(default=_HSTS_PRELOAD_MIN_AGE_SECONDS, ge=0)
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False

    # CORS：默认空列表表示拒绝所有跨域来源，必须显式配置可信来源。
    cors_allowed_origins: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = False
    cors_allow_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    cors_allow_headers: list[str] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "X-Request-Id"]
    )
    cors_expose_headers: list[str] = Field(
        default_factory=lambda: [
            "X-Request-Id",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "ETag",
            "Idempotent-Replayed",
        ]
    )
    cors_max_age_seconds: int = Field(default=600, ge=0)

    # 限流：按客户端 IP 的滑动窗口。认证路径使用更严格的桶。
    # ``memory`` 为进程内实现（默认）；多副本部署需提供 Redis 实现后切换。
    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_auth_path_prefixes: list[str] = Field(default_factory=lambda: ["/api/v1/auth"])
    rate_limit_auth_requests: int = Field(default=20, ge=1)
    rate_limit_auth_window_seconds: int = Field(default=60, ge=1)
    rate_limit_write_requests: int = Field(default=120, ge=1)
    rate_limit_write_window_seconds: int = Field(default=60, ge=1)
    # 默认不信任 X-Forwarded-For（可伪造）。仅在可信反向代理之后显式开启。
    rate_limit_trust_forwarded_for: bool = False

    @property
    def is_local(self) -> bool:
        return self.env.lower() in {"local", "dev", "development"}

    @property
    def has_complete_jwt_config(self) -> bool:
        return not self._jwt_config_problems()

    def validate_runtime_security(self) -> None:
        """启动自检：配置不安全时 fail-closed。

        - 非本地环境必须配置完整 JWT，且禁止 Header 身份后门；
        - 非本地环境禁止默认/占位 MinIO 凭据；
        - CORS 来源必须显式且格式合法；
        - 限流后端若选择尚未实现的 redis，直接拒绝启动（占位）。
        """
        problems = self._cors_config_problems()
        problems.extend(self._rate_limit_config_problems())
        problems.extend(self._security_headers_config_problems())
        problems.extend(self._validation_worker_config_problems())
        problems.extend(self._export_worker_config_problems())
        problems.extend(self._training_adapter_config_problems())
        if not self.is_local:
            if self.local_header_auth:
                problems.append(
                    "非本地环境禁止启用本地 Header 认证（DDP_LOCAL_HEADER_AUTH），"
                    "请改用 Bearer JWT"
                )
            problems.extend(self._jwt_config_problems())
            problems.extend(self._s3_credential_problems())
        if problems:
            raise RuntimeError("安全配置不完整，拒绝启动：" + "；".join(problems) + "。")

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

    def _s3_credential_problems(self) -> list[str]:
        problems: list[str] = []
        access_key = self.s3_access_key.strip()
        secret_key = self.s3_secret_key.strip()
        if access_key.lower() in _PLACEHOLDER_S3_CREDENTIALS:
            problems.append("DDP_S3_ACCESS_KEY 为空或仍为默认占位值")
        if secret_key.lower() in _PLACEHOLDER_S3_CREDENTIALS:
            problems.append("DDP_S3_SECRET_KEY 为空或仍为默认占位值")
        if access_key != self.s3_access_key or secret_key != self.s3_secret_key:
            problems.append("DDP_S3_ACCESS_KEY/DDP_S3_SECRET_KEY 不得包含首尾空白")
        if access_key and access_key == secret_key:
            problems.append("DDP_S3_ACCESS_KEY 与 DDP_S3_SECRET_KEY 不得相同")
        return problems

    def _cors_config_problems(self) -> list[str]:
        problems: list[str] = []
        for origin in self.cors_allowed_origins:
            if origin != origin.strip():
                problems.append(f"CORS 来源不得包含首尾空白：{origin!r}")
                continue
            if origin == "*":
                if not self.is_local:
                    problems.append("非本地环境 CORS 不得使用通配符 *，必须显式列出可信来源")
                elif self.cors_allow_credentials:
                    problems.append("CORS 允许凭据时不得使用通配符 *")
                continue
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                problems.append(f"CORS 来源格式非法（必须为 scheme://host[:port]）：{origin!r}")
        if len(set(self.cors_allowed_origins)) != len(self.cors_allowed_origins):
            problems.append("CORS 来源存在重复项")
        return problems

    def _rate_limit_config_problems(self) -> list[str]:
        problems: list[str] = []
        if self.rate_limit_enabled and self.rate_limit_backend == "redis":
            problems.append(
                "DDP_RATE_LIMIT_BACKEND=redis 仅为占位，尚未实现分布式限流；"
                "请使用 memory 或提供 Redis 实现"
            )
        for prefix in self.rate_limit_auth_path_prefixes:
            if not prefix.startswith("/") or prefix != prefix.strip() or not prefix.rstrip("/"):
                problems.append(f"限流认证路径前缀非法：{prefix!r}")
        return problems

    def _security_headers_config_problems(self) -> list[str]:
        problems: list[str] = []
        if self.hsts_enabled and self.hsts_max_age_seconds <= 0:
            problems.append("启用 HSTS 时 DDP_HSTS_MAX_AGE_SECONDS 必须大于 0")
        if self.hsts_enabled and self.hsts_preload:
            if not self.hsts_include_subdomains:
                problems.append("HSTS preload 必须同时启用 includeSubDomains")
            if self.hsts_max_age_seconds < _HSTS_PRELOAD_MIN_AGE_SECONDS:
                problems.append(
                    f"HSTS preload 的 max-age 不得小于 {_HSTS_PRELOAD_MIN_AGE_SECONDS} 秒"
                )
        return problems

    def _validation_worker_config_problems(self) -> list[str]:
        if self.validation_retry_max_seconds < self.validation_retry_base_seconds:
            return ["DDP_VALIDATION_RETRY_MAX_SECONDS 不得小于 DDP_VALIDATION_RETRY_BASE_SECONDS"]
        return []

    def _export_worker_config_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.export_storage_prefix.strip("/"):
            problems.append("DDP_EXPORT_STORAGE_PREFIX 不能为空")
        if self.export_default_timeout_seconds > self.export_max_timeout_seconds:
            problems.append(
                "DDP_EXPORT_DEFAULT_TIMEOUT_SECONDS 不得大于 DDP_EXPORT_MAX_TIMEOUT_SECONDS"
            )
        if self.export_default_max_attempts > self.export_max_attempts_limit:
            problems.append(
                "DDP_EXPORT_DEFAULT_MAX_ATTEMPTS 不得大于 DDP_EXPORT_MAX_ATTEMPTS_LIMIT"
            )
        if self.export_worker_retry_max_seconds < self.export_worker_retry_base_seconds:
            problems.append(
                "DDP_EXPORT_WORKER_RETRY_MAX_SECONDS 不得小于 "
                "DDP_EXPORT_WORKER_RETRY_BASE_SECONDS"
            )
        return problems

    def _training_adapter_config_problems(self) -> list[str]:
        problems: list[str] = []
        known_capabilities = {
            "submit",
            "status",
            "cancel",
            "callback",
            "model_ref",
            "evaluation_ref",
        }
        for adapter_id, secret in self.training_adapter_shared_secrets.items():
            if not adapter_id.strip() or adapter_id != adapter_id.strip():
                problems.append("训练 Adapter ID 不得为空或包含首尾空白")
            if not secret or secret != secret.strip():
                problems.append(f"训练 Adapter {adapter_id!r} 的回调密钥为空或含首尾空白")
            elif not self.is_local and len(secret) < 32:
                problems.append(f"非本地环境训练 Adapter {adapter_id!r} 的回调密钥长度不足 32")
        for adapter_id, capabilities in self.training_adapter_capabilities.items():
            if not adapter_id.strip() or adapter_id != adapter_id.strip():
                problems.append("训练 Adapter 能力声明的 ID 非法")
            unknown = set(capabilities) - known_capabilities
            if unknown:
                problems.append(
                    f"训练 Adapter {adapter_id!r} 声明了未知能力：{', '.join(sorted(unknown))}"
                )
            if len(set(capabilities)) != len(capabilities):
                problems.append(f"训练 Adapter {adapter_id!r} 能力声明存在重复项")
        if self.training_callback_nonce_ttl_seconds < self.training_callback_time_window_seconds:
            problems.append("DDP_TRAINING_CALLBACK_NONCE_TTL_SECONDS 不得小于回调时间窗")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
