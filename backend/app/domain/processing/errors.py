"""处理运行领域异常。

领域层不依赖 HTTP；API 层把稳定 ``code`` 映射为契约错误码与状态码。
本域**不新增**错误码：只复用既有的 ``validation_failed`` /
``state_transition_invalid`` / ``resource_not_found`` / ``version_conflict``。
"""

from __future__ import annotations


class ProcessingDomainError(ValueError):
    """处理运行域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(ProcessingDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(ProcessingDomainError):
    """当前状态不允许该动作。"""

    code = "state_transition_invalid"

    def __init__(self, current: object, action: object) -> None:
        self.current = current
        self.action = action
        super().__init__(f"处理运行当前状态 {current} 不允许动作 {action}")


class ResourceNotFoundError(ProcessingDomainError):
    """运行不存在或不属于当前项目。"""

    code = "resource_not_found"


class VersionConflictError(ProcessingDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str = "处理运行") -> None:
        self.resource = resource
        super().__init__(f"{resource}版本冲突")


__all__ = [
    "DomainValidationError",
    "InvalidStateTransitionError",
    "ProcessingDomainError",
    "ResourceNotFoundError",
    "VersionConflictError",
]
