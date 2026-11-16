"""场地域异常。

领域层不依赖 HTTP；API 层负责把稳定 ``code`` 映射为契约错误码与状态码。
本域的错误码取值与既有六域保持一致（``validation_failed`` /
``resource_not_found`` / ``version_conflict`` / ``duplicate_resource``），
因此 ``app/api/errors.py`` 只需新增一张查表，不引入新错误码。
"""

from __future__ import annotations


class LocationDomainError(ValueError):
    """场地域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(LocationDomainError):
    """字段或跨字段校验失败。"""


class ResourceNotFoundError(LocationDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        # 与采集域一致：领域消息不回显资源 ID，避免进入日志后泄漏客户端输入。
        super().__init__(f"{resource}不存在或不可见")


class DuplicateResourceError(LocationDomainError):
    """项目内业务键重复。"""

    code = "duplicate_resource"


class IdempotencyKeyReusedError(LocationDomainError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"


class VersionConflictError(LocationDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


__all__ = [
    "DomainValidationError",
    "DuplicateResourceError",
    "IdempotencyKeyReusedError",
    "LocationDomainError",
    "ResourceNotFoundError",
    "VersionConflictError",
]
