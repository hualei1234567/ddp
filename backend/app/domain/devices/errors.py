"""设备域异常。

领域层不依赖 HTTP；API 层负责把异常映射为契约错误码：
``state_transition_invalid``、``duplicate_resource``、``version_conflict`` 等。
"""

from __future__ import annotations


class DeviceDomainError(ValueError):
    """设备域可预期错误的基类。"""


class DomainValidationError(DeviceDomainError):
    """字段或跨字段校验失败。"""


class InvalidStateTransitionError(DeviceDomainError):
    """非法状态迁移；未知状态同样拒绝，绝不默认成功。"""

    def __init__(self, entity: str, current: object, target: object) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity} 不允许状态迁移：{current} → {target}")


class DuplicateResourceError(DeviceDomainError):
    """项目内业务键或序列号重复。"""


class ResourceNotFoundError(DeviceDomainError):
    """资源不存在或不属于当前项目。"""

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource} 不存在或不可见：{resource_id}")


class VersionConflictError(DeviceDomainError):
    """乐观锁版本不符。"""

    def __init__(self, resource: str, expected: int) -> None:
        self.resource = resource
        self.expected = expected
        super().__init__(f"{resource} 版本冲突：expected_version={expected}")
