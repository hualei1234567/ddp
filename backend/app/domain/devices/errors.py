"""设备域异常。

领域层不依赖 HTTP；API 层负责把异常映射为契约错误码：
``state_transition_invalid``、``duplicate_resource``、``version_conflict`` 等。
"""

from __future__ import annotations

from collections.abc import Mapping


class DeviceDomainError(ValueError):
    """设备域可预期错误的基类。

    ``code`` 是稳定的领域错误码，供 API 层映射为契约错误码；
    领域层不决定 HTTP 状态码。

    ``details`` 是**可在安全前提下回显给客户端**的结构化排障信息
    （例如参数校验失败的字段路径）。是否透传由 API 层按 code 白名单决定，
    领域层只负责如实携带，不判断安全性（G-19）。
    """

    code = "validation_failed"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details: Mapping[str, object] | None = details


class DomainValidationError(DeviceDomainError):
    """字段或跨字段校验失败。"""


class CalibrationChecksumMismatchError(DomainValidationError):
    """标定内容的服务端重算结果与已存 checksum 不一致。

    契约 §8.6 把它单列为 ``calibration_checksum_mismatch``：这不是"字段填错"，
    而是**已落库内容被绕过用例层改过**。与 ``validation_failed`` 混用会让
    调用方把篡改误判成输入格式问题，从而只想"改改请求再试"。
    """

    code = "calibration_checksum_mismatch"


class CalibrationActiveConflictError(DeviceDomainError):
    """同目标/同类型已有 ACTIVE，且**无法原子替代**（契约 §8.6）。

    注意区分两种情形：
    - 能原子替代（正常路径）：在 savepoint 内把旧 ACTIVE 转 SUPERSEDED，
      再把新的转 ACTIVE。这是状态机允许的合法迁移，不报错。
    - 无法原子替代（本异常）：并发窗口导致两条记录同时想成为 ACTIVE，
      被 ``uq_calibration_records_active_target`` 部分唯一索引挡住。
      此时必须让调用方知道"这是同目标激活冲突"，而不是笼统的业务键重复。
    """

    code = "calibration_active_conflict"


class CalibrationInvalidError(DomainValidationError):
    """标定数据本身不满足可用条件（如尚未生效/已过期时被激活）。

    与 ``validation_failed`` 的区别：请求形状合法，是**标定数据的状态**不允许
    该动作。契约把 VALIDATED→ACTIVE 的这类拒绝登记为 ``calibration_invalid``。
    """

    code = "calibration_invalid"


class CalibrationNotActiveError(DomainValidationError):
    """要求的标定类型在目标上没有 ACTIVE 记录，动作被拒绝。

    触发点：设备激活时，其 Profile ``required_types`` 声明的标定类型必须有
    ACTIVE 记录；缺任何一类都不得进入 ACTIVE。
    """

    code = "calibration_not_active"


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


class IdempotencyKeyReusedError(DomainValidationError):
    """同一幂等键对应不同请求体。"""

    code = "idempotency_key_reused"


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
