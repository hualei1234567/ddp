"""请求 ID 中间件。

优先级最高的用户中间件：为每个请求生成/透传 ``X-Request-Id``，
后续限流等中间件产生的错误响应也能带上请求 ID。

## request_id 与 trace_id 的关系

本项目**不引入 OpenTelemetry**（当前无 exporter，引入即多一套依赖与配置而无收益）。
在单服务边界内，一次 HTTP 请求就是最自然的追踪单元，因此把 ``request_id`` 同时
作为 ``trace_id`` 使用：

- ``request_id``：对外契约字段，出现在响应头 ``X-Request-Id`` 与错误体
  ``error.request_id``（``packages/contracts/schemas/error-envelope.json`` 已定义）；
- ``trace_id``：**内部**关联字段，只写日志上下文，不新增响应字段。

两者取值相同，但语义不同：``request_id`` 是对外承诺，改变它要动契约；``trace_id``
是内部约定，将来真接入分布式追踪时改为从 ``traceparent`` 提取、与 ``request_id``
分离，不会破坏客户端。因此当前错误体只保留 ``request_id``，不重复加 ``trace_id``
——同一值两个键只会让客户端困惑。

日志侧：这里设置 ``contextvars`` 中的 trace_id，由 ``app.core.logging.JsonFormatter``
统一读取并写入每条日志记录，业务代码无需手工传参。
"""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import is_safe_trace_id, trace_id_var


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming = request.headers.get("X-Request-Id")
        # 校验规则由 ``app.core.logging.is_safe_trace_id`` 单点定义，Worker
        # 恢复事件上下文时复用同一规则，避免两处口径漂移。
        request_id = incoming if is_safe_trace_id(incoming) else uuid4().hex
        request.state.request_id = request_id
        # 与 request_id 同值（见模块注释）；用 token 复位，避免 contextvar
        # 在连接复用/任务切换时泄漏到下一个请求。
        token = trace_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response
