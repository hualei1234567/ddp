"""限流中间件。

策略（可通过 Settings 调整）：
- 认证路径（默认 ``/api/v1/auth`` 前缀）：严格桶，按客户端 IP；
- ``/api/v1`` 下的写方法（POST/PUT/PATCH/DELETE）：写桶，按客户端 IP。

按 IP 计数可防止通过轮换令牌绕过限流，代价是 NAT 后用户共享配额；
需要按主体限流时，应在认证完成后由应用层依赖接入同一 ``RateLimiter``。
``X-Forwarded-For`` 默认不信任，只有部署在可信代理之后才可显式开启。

说明：当前项目的令牌由外部身份源签发，``/api/v1/auth`` 前缀是为后续
登录/刷新接口预留的严格桶。分布式限流尚未实现，``DDP_RATE_LIMIT_BACKEND``
只接受 ``memory``；协议约定见 ``app.infrastructure.rate_limit``。
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.infrastructure.rate_limit import RateLimiter

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_WRITE_PATH_PREFIX = "/api/v1"


@dataclass(frozen=True)
class _Policy:
    name: str
    limit: int
    window_seconds: int


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: RateLimiter,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        policy = self._policy_for(request)
        if policy is None:
            return await call_next(request)

        key = f"{policy.name}:{self._client_identifier(request)}"
        decision = self._limiter.check(
            key,
            limit=policy.limit,
            window_seconds=policy.window_seconds,
        )
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "请求过于频繁，请稍后重试",
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
                headers={
                    "Retry-After": str(decision.retry_after_seconds),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(decision.limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(decision.remaining))
        return response

    def _policy_for(self, request: Request) -> _Policy | None:
        path = request.url.path
        if any(
            _path_matches_prefix(path, prefix)
            for prefix in self._settings.rate_limit_auth_path_prefixes
        ):
            return _Policy(
                name="auth",
                limit=self._settings.rate_limit_auth_requests,
                window_seconds=self._settings.rate_limit_auth_window_seconds,
            )
        if request.method in _WRITE_METHODS and _path_matches_prefix(path, _WRITE_PATH_PREFIX):
            return _Policy(
                name="write",
                limit=self._settings.rate_limit_write_requests,
                window_seconds=self._settings.rate_limit_write_window_seconds,
            )
        return None

    def _client_identifier(self, request: Request) -> str:
        if self._settings.rate_limit_trust_forwarded_for:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                first = forwarded.split(",", 1)[0].strip()
                if first:
                    return first
        client = request.client
        return client.host if client is not None else "unknown"


def _path_matches_prefix(path: str, prefix: str) -> bool:
    """按路径段边界匹配，避免 ``/api/v1/authz`` 命中 ``/api/v1/auth``。"""
    normalized = prefix.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")
