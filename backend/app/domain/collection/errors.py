"""采集域异常。

领域层不依赖 HTTP；API 层负责把稳定 ``code`` 映射为契约错误码与状态码。
未知动作、未知状态、终态回退一律显式失败，不存在默认成功分支。
"""

from __future__ import annotations


class CollectionDomainError(ValueError):
    """采集任务/批次可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(CollectionDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(CollectionDomainError):
    """非法状态迁移或终态回退。"""

    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(CollectionDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        # 领域消息不回显资源 ID，避免进入日志后泄漏客户端输入。
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(CollectionDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


class ResourceImmutableError(CollectionDomainError):
    """资源当前状态不允许修改。"""

    code = "resource_immutable"


class DuplicateResourceError(CollectionDomainError):
    """项目内业务键重复。"""

    code = "duplicate_resource"


class ConflictError(CollectionDomainError):
    """跨对象一致性冲突，例如批次领先任务。"""

    code = "conflict"


class ForbiddenOperationError(CollectionDomainError):
    """职责分离或业务权限拒绝。"""

    code = "forbidden"


class IdempotencyKeyReusedError(CollectionDomainError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"
