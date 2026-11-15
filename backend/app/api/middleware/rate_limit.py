"""限流中间件。

策略（可通过 Settings 调整）：
- 认证路径（默认 ``/api/v1/auth`` 前缀）：严格桶，按客户端 IP；
- ``/api/v1`` 下的写方法（POST/PUT/PATCH/DELETE）：写桶，按客户端 IP。

按 IP 计数可防止通过轮换令牌绕过限流，代价是 NAT 后用户共享配额；
需要按主体限流时，应在认证完成后由应用层依赖接入同一 ``RateLimiter``。
``X-Forwarded-For`` 默认不信任，只有部署在可信代理之后才可显式开启。

后端可切换（``DDP_RATE_LIMIT_BACKEND``）：``memory`` 为进程内计数（默认，
单实例），``redis`` 为多副本共享的全局配额。两者协议一致，见
``app.infrastructure.rate_limit``；决策与取舍见 ``docs/adr/0031``。

``RateLimiter.check`` 是同步阻塞调用（Redis 场景涉及网络往返），因此统一
经 ``run_in_threadpool`` 执行，避免阻塞事件循环。连接超时与重试策略在
基础设施层显式收紧，保证最坏耗时有界。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.infrastructure.rate_limit import RateLimitDecision, RateLimiter

logger = logging.getLogger("ddp.rate_limit")

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

        client = self._client_identifier(request)
        key = f"{policy.name}:{client}"
        decision = await run_in_threadpool(
            self._limiter.check,
            key,
            limit=policy.limit,
            window_seconds=policy.window_seconds,
        )
        self._log_decision(request, policy, client, decision)

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

    def _log_decision(
        self,
        request: Request,
        policy: _Policy,
        client: str,
        decision: RateLimitDecision,
    ) -> None:
        """结构化记录限流动态，便于排障。

        来源标识一律脱敏（与内存键哈希同源），避免把客户端 IP 写进日志；
        桶名与窗口是排障必需信息，原样保留。
        """
        fields: dict[str, object] = {
            "bucket": policy.name,
            "backend": self._settings.rate_limit_backend,
            "window_seconds": policy.window_seconds,
            "limit": decision.limit,
            "remaining": decision.remaining,
            "method": request.method,
            "path": request.url.path,
            "client": client_fingerprint(client),
        }
        if decision.degraded:
            # 降级放行是保护措施失效，必须可告警，不能当成正常放行静默掉。
            logger.error("限流后端降级放行（fail-open） %s", fields)
            return
        if decision.allowed:
            logger.debug("限流放行 %s", fields)
            return
        fields["retry_after_seconds"] = decision.retry_after_seconds
        logger.warning("限流命中 %s", fields)

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


def client_fingerprint(identifier: str) -> str:
    """来源标识的稳定摘要，用于日志脱敏。

    同一来源在同一进程内得到同一摘要，可追踪"同一客户端短时间内的多次命中"，
    但无法从日志反推原始 IP。
    """
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def _path_matches_prefix(path: str, prefix: str) -> bool:
    """按路径段边界匹配，避免 ``/api/v1/authz`` 命中 ``/api/v1/auth``。"""
    normalized = prefix.rstrip("/")
    return path == normalized or path.startswith(normalized + "/")
