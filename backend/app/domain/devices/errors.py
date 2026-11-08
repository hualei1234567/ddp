"""设备域异常。

领域层不依赖 HTTP；API 层负责把异常映射为契约错误码：
``state_transition_invalid``、``duplicate_resource``、``version_conflict`` 等。
"""

from __future__ import annotations


class DeviceDomainError(ValueError):
    """设备域可预期错误的基类。

    ``code`` 是稳定的领域错误码，供 API 层映射为契约错误码；
    领域层不决定 HTTP 状态码。
    """

    code = "validation_failed"


class DomainValidationError(DeviceDomainError):
    """字段或跨字段校验失败。"""


class CalibrationTargetInvalidError(DomainValidationError):
    """标定目标不存在、跨项目或类型不匹配。"""

    code = "calibration_target_invalid"


class CalibrationProfileInvalidError(DomainValidationError):
    """标定配置不存在、跨项目或与记录不匹配。"""

    code = "calibration_profile_invalid"


class FourEyesViolationError(DomainValidationError):
    """标定执行人与校验人未分离。"""

    code = "forbidden"


class InvalidStateTransitionError(DeviceDomainError):
    """非法状态迁移；未知状态同样拒绝，绝不默认成功。"""

    code = "state_transition_invalid"

    def __init__(self, entity: str, current: object, target: object) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity} 不允许状态迁移：{current} → {target}")


class DuplicateResourceError(DeviceDomainError):
    """项目内业务键或序列号重复。"""

    code = "duplicate_resource"


class ResourceNotFoundError(DeviceDomainError):
    """资源不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(
        self,
        resource: str,
        resource_id: object,
        *,
        code: str | None = None,
    ) -> None:
        self.resource = resource
        self.resource_id = resource_id
        if code is not None:
            self.code = code
        # 领域消息不回显资源 ID，避免进入日志或异常链后泄漏客户端输入。
        super().__init__(f"{resource}不存在或不可见")


class VersionConflictError(DeviceDomainError):
    """乐观锁版本不符。"""

    code = "version_conflict"

    def __init__(self, resource: str, expected: int) -> None:
        self.resource = resource
        self.expected = expected
        # 不回显调用方传入的版本值；版本号细节只保留在领域属性中。
        super().__init__(f"{resource}版本冲突")


class ResourceImmutableError(DeviceDomainError):
    """不可变资源或不可变字段被修改；对外映射为 ``resource_immutable``。"""

    code = "resource_immutable"
