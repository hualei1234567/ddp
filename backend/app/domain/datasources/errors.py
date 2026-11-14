"""数据源域异常。

领域层不依赖 HTTP；API 层负责把稳定 ``code`` 映射为契约错误码与状态码。
未知状态、非法迁移、终态回退一律显式失败，不存在默认成功分支。
"""

from __future__ import annotations


class DataSourceDomainError(ValueError):
    """数据源域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(DataSourceDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(DataSourceDomainError):
    """非法状态迁移或终态回退。"""

    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, action: object) -> None:
        self.entity = entity
        self.current = current
        self.action = action
        super().__init__(f"{entity} 当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(DataSourceDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class DuplicateResourceError(DataSourceDomainError):
    """项目内业务键重复。"""

    code = "duplicate_resource"


class VersionConflictError(DataSourceDomainError):
    """乐观锁版本不匹配。"""

    code = "version_conflict"

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"{resource}版本已变化，请重新读取后再试")


class IdempotencyKeyReusedError(DomainValidationError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"
