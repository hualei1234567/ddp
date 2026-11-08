"""设备域字段与跨字段校验辅助函数。

只存放设备域专用规则，不提供跨模块万能工具。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import TypeVar
from uuid import UUID

from app.domain.devices.errors import DomainValidationError

EnumT = TypeVar("EnumT", bound=Enum)


def normalized_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} 必须是非空字符串")
    return value.strip()


def optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return normalized_text(value, field_name)


def coerce_enum(value: object, enum_type: type[EnumT], field_name: str) -> EnumT:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise DomainValidationError(f"{field_name} 取值非法：{value!r}，允许值：{allowed}") from exc


def frozen_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    return MappingProxyType(dict(value))


def aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区（UTC）")
    return value


def positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def positive_float(value: object, field_name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正数")
    return float(value)


def uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    """校验 UUID 元组；是否允许为空由调用方的状态规则决定。"""
    if not isinstance(value, tuple):
        raise DomainValidationError(f"{field_name} 必须是元组")
    for item in value:
        if not isinstance(item, UUID):
            raise DomainValidationError(f"{field_name} 只能包含 UUID")
    return value
