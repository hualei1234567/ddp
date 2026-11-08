"""安全响应头中间件。

对所有响应追加安全基线头：
- ``X-Content-Type-Options: nosniff``
- ``Referrer-Policy``（可配置，默认 ``no-referrer``）
- ``Content-Security-Policy``（API 基线）
- ``Strict-Transport-Security``（仅显式开启 HSTS 时）
另加 ``X-Frame-Options`` 与 ``Permissions-Policy`` 作为纵深防御。

FastAPI 自带文档页面依赖 CDN 资源，套用严格 CSP 会导致页面不可用；
因此文档与 OpenAPI 路径跳过 CSP，其余安全头仍然生效。生产环境建议
在反向代理层关闭或单独保护文档页面。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import Settings

_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", self._settings.referrer_policy)
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.url.path not in _CSP_EXEMPT_PATHS and self._settings.content_security_policy:
            headers.setdefault(
                "Content-Security-Policy",
                self._settings.content_security_policy,
            )
        if self._settings.hsts_enabled:
            headers.setdefault(
                "Strict-Transport-Security",
                self._hsts_value(),
            )
        return response

    def _hsts_value(self) -> str:
        value = f"max-age={self._settings.hsts_max_age_seconds}"
        if self._settings.hsts_include_subdomains:
            value += "; includeSubDomains"
        if self._settings.hsts_preload:
            value += "; preload"
        return value
