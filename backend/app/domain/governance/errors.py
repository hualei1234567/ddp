"""治理域异常。

领域层不依赖 HTTP；API 层把稳定 ``code`` 映射为契约错误码与状态码。
本域新增两个此前不存在的码：

- ``quota_exceeded``（409）：写操作因项目配额已满被拒绝；
- ``quota_measurement_unavailable``（503）：配置了配额但无法取得权威用量，
  按 fail-closed 拒绝写入——**不能**把"测不到"当成"没超"。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.governance.enums import ResourceKind


class GovernanceDomainError(ValueError):
    """治理域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(GovernanceDomainError):
    """字段或跨字段校验失败。"""


class ResourceNotFoundError(GovernanceDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        # 与既有域一致：领域消息不回显资源 ID，避免进入日志后泄漏客户端输入。
        super().__init__(f"{resource}不存在或不可见")


class DuplicateResourceError(GovernanceDomainError):
    """项目内同一资源类型只允许一条配额。"""

    code = "duplicate_resource"


class VersionConflictError(GovernanceDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class QuotaPreconditionRequiredError(GovernanceDomainError):
    """修改既有配额必须携带 ``If-Match``。"""

    code = "precondition_required"

    def __init__(self) -> None:
        super().__init__("修改既有配额必须携带 If-Match")


class QuotaExceededError(GovernanceDomainError):
    """写操作会突破项目配额。

    ``details`` 只含本项目内的资源类型、上限、当前用量与本次请求量——
    不含客户端输入原文，也不含其他项目的信息，因此可安全回显给客户端
    用于展示"还差多少"。
    """

    code = "quota_exceeded"

    def __init__(
        self,
        resource_kind: ResourceKind,
        *,
        limit: int,
        used: int,
        requested: int,
    ) -> None:
        self.resource_kind = resource_kind
        self.limit = limit
        self.used = used
        self.requested = requested
        super().__init__(f"项目配额已满：{resource_kind.value}")

    @property
    def details(self) -> Mapping[str, object]:
        return {
            "resource_kind": self.resource_kind.value,
            "limit": self.limit,
            "used": self.used,
            "requested": self.requested,
        }


class QuotaMeasurementUnavailableError(GovernanceDomainError):
    """配置了配额但无法取得权威用量。

    这是 fail-closed 的落点：宁可拒绝一次写入并留下 503，也不能在
    "不知道已经用了多少"的情况下放行——那会让配额形同虚设。
    """

    code = "quota_measurement_unavailable"

    def __init__(self, resource_kind: ResourceKind | None = None) -> None:
        self.resource_kind = resource_kind
        if resource_kind is None:
            super().__init__("无法统计项目当前用量")
        else:
            super().__init__(f"无法统计 {resource_kind.value} 的当前用量")


__all__ = [
    "DomainValidationError",
    "DuplicateResourceError",
    "GovernanceDomainError",
    "QuotaExceededError",
    "QuotaMeasurementUnavailableError",
    "QuotaPreconditionRequiredError",
    "ResourceNotFoundError",
    "VersionConflictError",
]
