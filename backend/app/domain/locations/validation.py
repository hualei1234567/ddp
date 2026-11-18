"""场地域字段校验。

为什么单独一份而不是复用 ``app.domain.collection.validation`` 或
``app.domain.devices.validation``：这两份都把错误类型绑死在本域异常上
（``CollectionDomainError`` / ``DeviceDomainError``），场地域复用它们会让
"场地字段不合法"抛出采集域或设备域异常，进而被 ``app/main.py`` 的错误处理器
映射成错误域的 code 与日志标签。这与仓库既有约定一致——每个域各有
``pagination.py`` / ``idempotency.py``，差异只在错误类型绑定，不在规则本身。

只放本域实际用到的四个函数，不提供跨模块万能工具。错误文案只含字段名与固定
规则，不拼接客户端原始值。
"""

from __future__ import annotations

from uuid import UUID

from app.domain.locations.errors import DomainValidationError

CODE_MAX_LENGTH = 64
NAME_MAX_LENGTH = 200
ADDRESS_MAX_LENGTH = 500
NOTES_MAX_LENGTH = 2000


def normalized_text(
    value: object,
    field_name: str,
    *,
    max_length: int = NAME_MAX_LENGTH,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} 必须是非空字符串")
    text = value.strip()
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 长度不得超过 {max_length} 个字符")
    return text


def optional_text(
    value: object,
    field_name: str,
    *,
    max_length: int = NAME_MAX_LENGTH,
) -> str | None:
    if value is None:
        return None
    return normalized_text(value, field_name, max_length=max_length)


def required_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def non_negative_int(value: object, field_name: str) -> int:
    """只读计数允许 0：空基地必须如实返回 0，而不是用缺失字段掩盖。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


__all__ = [
    "ADDRESS_MAX_LENGTH",
    "CODE_MAX_LENGTH",
    "NAME_MAX_LENGTH",
    "NOTES_MAX_LENGTH",
    "non_negative_int",
    "normalized_text",
    "optional_text",
    "positive_int",
    "required_uuid",
]
