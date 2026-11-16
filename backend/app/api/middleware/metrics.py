"""HTTP 埋点中间件。

纯 ASGI 实现（不用 ``BaseHTTPMiddleware``）：后者会用 anyio 内存流重新包装响应，
对流式响应有额外开销，而这里只需要看 ``http.response.start`` 的一个状态码。

位置（见 ``app/main.py`` 的装配顺序）：路由之外的中间件才能读到
``scope["route"]`` 里的路由模板；位于限流中间件**内层**，因此被限流拒绝的请求
也会计入（此时无路由匹配，落在 ``__unmatched__`` 桶）。``/health`` 与
``/metrics`` 本身不埋点，理由见 ``app.core.metrics`` 模块注释。
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import EXEMPT_PATHS, Metrics

# 没有发出 ``http.response.start`` 就结束时（异常穿透到最外层）按 5xx 计。
_UNHANDLED_ERROR_STATUS = 500


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        method = self.metrics.method_label(scope.get("method", ""))
        status = _UNHANDLED_ERROR_STATUS

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            self.metrics.observe_request(
                method=method,
                route=self.metrics.route_label_from_scope(scope),
                status=status,
                duration_seconds=time.perf_counter() - started,
            )
