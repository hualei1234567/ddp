"""HTTP 服务端 span 中间件。

纯 ASGI 实现（与 ``app/api/middleware/metrics.py`` 同款，不用
``BaseHTTPMiddleware``）：后者会用 anyio 内存流重新包装响应，对流式响应有额外
开销，而这里只需要读 ``http.response.start`` 的一个状态码。

## 关闭态 = 完全旁路（硬约束）

``DDP_OTEL_ENABLED=false``（默认）时本中间件**只做一次布尔判断**就原样透传：
不产生 span、不改响应头、不改 ``trace_id`` 上下文变量。请求路径上的行为与
接入前逐字节一致。

## span 名用路由模板，不用原始 URL

span 名是 OTel 里最高危的高基数维度：原始 URL 含 UUID/查询串，用它当 span 名
会让链路后端（Jaeger/Tempo）的索引迅速膨胀。规则与 Prometheus 指标**共用同一处
权威实现**：``app/core/metrics.py`` 的 ``route_label_from_scope`` / ``method_label``
——即中间件直接复用 ``Metrics`` 实例，不另写一套防基数逻辑。

难点在于**时机**：Starlette 在路由匹配成功后才把命中的 Route 写回
``scope["route"]``（``scope.update(child_scope)``），因此请求**开始时**拿不到
路由模板，只有响应返回后才拿得到。本中间件的做法与
``app/api/middleware/metrics.py`` 一致：先用有界的临时名开 span，响应返回后再用
``update_name`` 改成模板名。

- 命中路由：span 名为 ``GET /api/v1/projects/{project_id}/devices``；
- 未命中路由（404、限流短路、尾斜杠重定向）：路由模板拿不到，用有界的兜底名
  ``HTTP {method}``（method 本身也经 ``method_label`` 折叠未知方法），
  **绝不进原始 URL**。

## 上下文的来与去

- **入**：请求头里的 W3C ``traceparent``/``tracestate`` → 提取为父上下文，
  使本服务的 span 成为上游 span 的子节点（跨服务调用链由此串联）；
- **出**：响应头加 ``X-Trace-Id``（32 位小写 hex），便于把前端/网关侧的报错
  与后端 trace 对上。

## 与日志 ``trace_id`` 的关系

日志里的 ``trace_id`` 仍由 ``app/core/logging.py`` 的 ``trace_id_var`` 承载
（值等于 ``request_id``，是既有的对外关联字段）。本中间件**不改**它——它是对外
契约字段，改动要动契约。追踪开启时两者并存且可互相定位：``X-Trace-Id`` 给
链路，``X-Request-Id`` 给日志。
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import UNMATCHED_ROUTE, Metrics
from app.core.telemetry import current_trace_id, extract_context, is_enabled, server_span

logger = logging.getLogger("ddp.telemetry.http")

#: 响应头名：回传 trace id，供调用方在链路系统里定位本次请求。
TRACE_ID_HEADER = "X-Trace-Id"

#: 未命中路由时的兜底 span 名模板。刻意**不含路径**：未匹配路径完全由客户端
#: 控制（可无限随机），进 span 名会立刻把链路后端撑爆。
UNMATCHED_SPAN_NAME = "HTTP {method}"

#: 开 span 时的临时名。路由模板在响应返回后才拿得到（见模块注释），
#: 因此先用一个有界占位，之后再 ``update_name``。
PENDING_SPAN_NAME = "HTTP pending"


class TelemetryMiddleware:
    """为每个请求开启服务端 span，并回传 ``X-Trace-Id``。

    位置（见 ``app/main.py`` 的装配顺序）：与 ``MetricsMiddleware`` 同层，位于
    路由之外才能在响应后读到 ``scope["route"]`` 里的路由模板。
    """

    def __init__(self, app: ASGIApp, *, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_enabled():
            # 关闭态：原样透传，不产生 span、不改响应头。
            await self.app(scope, receive, send)
            return

        method = self.metrics.method_label(scope.get("method", ""))
        status = 0

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                trace_id = current_trace_id()
                if trace_id:
                    raw_headers = list(message.get("headers", []))
                    raw_headers.append((b"x-trace-id", trace_id.encode("ascii")))
                    message["headers"] = raw_headers
            await send(message)

        parent_context = extract_context(_incoming_headers(scope))
        with server_span(
            name=PENDING_SPAN_NAME,
            attributes={
                "http.request.method": scope.get("method", ""),
                "url.path": scope.get("path", ""),
            },
            parent_context=parent_context,
        ) as span:
            try:
                await self.app(scope, receive, send_with_status)
            finally:
                if span is not None:
                    self._finalize(span, scope=scope, method=method, status=status)

    def _finalize(self, span: object, *, scope: Scope, method: str, status: int) -> None:
        """响应结束后补齐 span 的路由模板名与状态码。"""
        template = self.metrics.route_label_from_scope(scope)
        if template == UNMATCHED_ROUTE:
            span.update_name(UNMATCHED_SPAN_NAME.format(method=method))
        else:
            span.update_name(f"{method} {template}")
        span.set_attribute("http.route", template)
        if status:
            span.set_attribute("http.response.status_code", status)


def _incoming_headers(scope: Scope) -> dict[str, str]:
    """把 ASGI 原始请求头转成 ``{name: value}``（供 W3C 上下文提取）。"""
    headers: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        # 同名头取第一个：W3C 规范要求每个头只出现一次。
        headers.setdefault(name, value)
    return headers


__all__ = [
    "PENDING_SPAN_NAME",
    "TRACE_ID_HEADER",
    "UNMATCHED_SPAN_NAME",
    "TelemetryMiddleware",
]
