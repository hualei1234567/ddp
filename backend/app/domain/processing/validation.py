"""处理运行域的字段级校验原语。

N4-001 的 :mod:`app.domain.processing.models` 与 N4-002 的
:mod:`app.domain.processing.steps` 都需要同一套"UUID / 文本 / 带时区时间 /
非负整数"校验。规则只在这里实现一次：两个实体各自复制一份的话，
迟早会出现"运行能接受、步骤不接受"的口径分叉。

这里只做**字段级**校验（类型、长度、时区、正负），跨字段不变量仍留在
各自实体里。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.domain.processing.errors import DomainValidationError


def required_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def required_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    cleaned = value.strip()
    if not cleaned:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field_name} 超过最大长度 {max_length}")
    return cleaned


def optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return required_text(value, field_name, max_length=max_length)


def optional_aware(value: object, field_name: str) -> datetime | None:
    """可空时间字段必须是带时区时间；naive 时间一律拒绝。"""
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须是带时区时间")
    return value


def non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


__all__ = [
    "non_negative_int",
    "optional_aware",
    "optional_text",
    "positive_int",
    "required_text",
    "required_uuid",
]
