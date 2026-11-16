"""审计查询域异常。

审计域只有读路径，因此只需要一个 ``validation_failed`` 语义的错误码：
分页参数与过滤条件非法。错误码与既有各域保持一致，``app/api/errors.py``
只需新增一张查表，不引入新的契约错误码。
"""

from __future__ import annotations


class AuditDomainError(ValueError):
    """审计查询域可预期错误的基类。"""

    code = "validation_failed"


class DomainValidationError(AuditDomainError):
    """分页参数或过滤条件不合法。"""


__all__ = ["AuditDomainError", "DomainValidationError"]
