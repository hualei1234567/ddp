"""统一业务异常。

对外错误体固定为::

    {"error": {"code": "...", "message": "...", "request_id": "..."}}
"""

from __future__ import annotations


class AppError(Exception):
    """可预期的业务错误；由全局异常处理器转换为统一响应。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__("not_found", message, 404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权访问") -> None:
        super().__init__("forbidden", message, 403)


class ConflictError(AppError):
    def __init__(self, message: str = "资源状态冲突") -> None:
        super().__init__("conflict", message, 409)
