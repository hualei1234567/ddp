"""请求 ID 中间件。

优先级最高的用户中间件：为每个请求生成/透传 ``X-Request-Id``，
后续限流等中间件产生的错误响应也能带上请求 ID。
"""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_MAX_REQUEST_ID_LENGTH = 128


def _is_safe_request_id(value: str | None) -> bool:
    """只接受长度受限的可打印 ASCII，避免日志/响应头注入。"""
    if not value or len(value) > _MAX_REQUEST_ID_LENGTH:
        return False
    return all(32 <= ord(char) < 127 for char in value)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming = request.headers.get("X-Request-Id")
        request_id = incoming if _is_safe_request_id(incoming) else uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
