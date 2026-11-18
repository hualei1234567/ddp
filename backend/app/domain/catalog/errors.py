"""资产目录领域异常。

领域层不依赖 HTTP；API 层负责把稳定 ``code`` 映射为契约错误码与状态码。
本域只有只读检索，唯一可预期错误是检索参数非法（``validation_failed``）。
"""

from __future__ import annotations


class CatalogDomainError(ValueError):
    """资产目录域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(CatalogDomainError):
    """检索词、类型过滤或游标非法。"""


__all__ = ["CatalogDomainError", "DomainValidationError"]
