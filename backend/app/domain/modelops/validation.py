"""阶段 5 领域字段校验辅助；只做通用校验，不承载业务规则。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar
from uuid import UUID

from app.domain.modelops.errors import DomainValidationError

_EnumT = TypeVar("_EnumT", bound=StrEnum)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def text_value(value: object, field: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field} 必须是字符串")
    cleaned = value.strip()
    if cleaned == "":
        raise DomainValidationError(f"{field} 不能为空")
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field} 超过最大长度 {max_length}")
    return cleaned


def optional_text(value: object, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return text_value(value, field, max_length=max_length)


def required_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field} 必须是 UUID")
    return value


def optional_uuid(value: object, field: str) -> UUID | None:
    if value is None:
        return None
    return required_uuid(value, field)


def positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainValidationError(f"{field} 必须是正整数")
    return value


def non_negative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field} 必须是非负整数")
    return value


def coerce_enum(value: object, enum_type: type[_EnumT], field: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise DomainValidationError(f"{field} 取值非法") from exc


def sha256_value(value: object, field: str) -> str:
    text = text_value(value, field, max_length=80)
    if not _SHA256.fullmatch(text):
        raise DomainValidationError(f"{field} 必须是 sha256:<64 位小写十六进制>")
    return text


def aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field} 必须是时间")
    if value.tzinfo is None:
        raise DomainValidationError(f"{field} 必须带时区")
    return value.astimezone(UTC)


def optional_aware_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return aware_datetime(value, field)


def string_list(
    value: object,
    field: str,
    *,
    max_length: int,
    min_items: int = 0,
) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field} 必须是数组")
    result: list[str] = []
    for item in value:
        cleaned = text_value(item, field, max_length=max_length)
        if cleaned not in result:
            result.append(cleaned)
    if len(result) < min_items:
        raise DomainValidationError(f"{field} 至少需要 {min_items} 项")
    return result


def number_map(value: object, field: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise DomainValidationError(f"{field} 必须是对象")
    result: dict[str, float] = {}
    for key, item in value.items():
        name = text_value(key, f"{field}.key", max_length=100)
        if not isinstance(item, int | float) or isinstance(item, bool):
            raise DomainValidationError(f"{field}.{name} 必须是数字")
        result[name] = float(item)
    return result
