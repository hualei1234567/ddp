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


class QualityEngineError(RuntimeError):
    """规则源与声明不一致、规则返回了非法结果等服务端缺陷。

    **不得**被静默吞掉或降级成"通过"：出现该异常说明质量结论已不可信。
    """


__all__ = [
    "DomainValidationError",
    "QualityDomainError",
    "QualityEngineError",
    "ResourceNotFoundError",
]
