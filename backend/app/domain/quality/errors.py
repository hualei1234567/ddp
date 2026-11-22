"""质量引擎领域异常。

领域层不依赖 HTTP；API 层把稳定 ``code`` 映射为契约错误码与状态码。
本域**不新增**错误码：复用既有的 ``validation_failed`` /
``resource_not_found``，避免为一次编排新增一套错误命名空间。

``QualityEngineError`` 刻意不继承 :class:`QualityDomainError`：规则实现与
规则声明不一致属于服务端缺陷（500 internal_error），不是客户端请求错误。
把它伪装成 422 会让排障方向从"代码有问题"偏成"参数有问题"。
"""

from __future__ import annotations


class QualityDomainError(ValueError):
    """质量域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(QualityDomainError):
    """字段、枚举或跨字段校验失败。"""


class ResourceNotFoundError(QualityDomainError):
    """检查对象不存在或不属于当前项目。"""

    code = "resource_not_found"

    def __init__(self, resource: str, resource_id: object) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource}不存在或不可见")


class InvalidQualityRunTransitionError(QualityDomainError):
    """非法的运行生命周期迁移。

    沿用父类的 ``validation_failed``（422 语义）：调用方拿到了一个不允许的
    状态迁移，属于请求/编排错误，不是服务端规则缺陷。
    """

    def __init__(self, current: object, target: object) -> None:
        self.current = current
        self.target = target
        super().__init__(f"质量运行不允许从 {current} 迁移到 {target}")


class QualityRunConflictError(QualityDomainError):
    """并发写入：目标行已被其他执行者推进，本次迁移的前提不再成立。

    不复用 :class:`InvalidQualityRunTransitionError`：那是"这条边本来就不该
    走"，属于编排错误；这里是"这条边本来可以走，但行已经不是我以为的状态"，
    属于并发冲突，调用方应重新读取而不是修正调用。

    ``version_conflict`` 是既有的契约错误码（409），本域不新增命名空间。
    """

    code = "version_conflict"

    def __init__(self, run_id: object, expected: object) -> None:
        self.run_id = run_id
        self.expected = expected
        super().__init__(f"质量运行 {run_id} 已不在 {expected} 状态，请重新读取后再试")


class QualityEngineError(RuntimeError):
    """规则源与声明不一致、规则返回了非法结果等服务端缺陷。

    **不得**被静默吞掉或降级成"通过"：出现该异常说明质量结论已不可信。
    """


__all__ = [
    "DomainValidationError",
    "InvalidQualityRunTransitionError",
    "QualityDomainError",
    "QualityEngineError",
    "QualityRunConflictError",
    "ResourceNotFoundError",
]
