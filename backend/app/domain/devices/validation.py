"""设备域字段与跨字段校验辅助函数。

只存放设备域专用规则，不提供跨模块万能工具。所有错误文案只包含字段名
与固定规则描述，不拼接客户端提交的原始值，避免输入回显与日志泄漏。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import TypeVar
from uuid import UUID

from app.domain.devices.errors import DomainValidationError

EnumT = TypeVar("EnumT", bound=Enum)

# 字段长度上限与数据库 ``String(n)``、外部契约保持一致；集中定义便于审查。
CODE_MAX_LENGTH = 64
NAME_MAX_LENGTH = 200
LABEL_MAX_LENGTH = 120
VERSION_MAX_LENGTH = 64
METHOD_MAX_LENGTH = 200
CHECKSUM_MAX_LENGTH = 128
FRAME_MAX_LENGTH = 200
SOURCE_MAX_LENGTH = 120

# 结构化 JSON 字段的防滥用上限：序列化字节、嵌套深度、对象键总数。
JSON_MAX_BYTES = 64 * 1024
JSON_MAX_DEPTH = 8
JSON_MAX_KEYS = 128


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


def coerce_enum(value: object, enum_type: type[EnumT], field_name: str) -> EnumT:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc


def frozen_mapping(value: object, field_name: str) -> Mapping[str, object]:
    """校验 JSON 对象并冻结顶层，同时深拷贝值，避免外部可变对象穿透。

    嵌套容器保持原有 JSON 形状（dict/list/tuple），以便仓储与校验和保持一致；
    深拷贝保证调用方后续修改原始对象不会影响领域实体。
    """
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")

    key_count = _validate_json_value(value, field_name, depth=0)
    if key_count > JSON_MAX_KEYS:
        raise DomainValidationError(f"{field_name} 的对象键总数不得超过 {JSON_MAX_KEYS} 个")

    copied = _copy_json_value(value)
    try:
        encoded = json.dumps(
            copied,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 对象") from exc
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后不得超过 {JSON_MAX_BYTES} 字节")
    return MappingProxyType(copied)


def _validate_json_value(value: object, field_name: str, *, depth: int) -> int:
    """递归校验 JSON 值，返回子树内对象键总数。"""
    if depth > JSON_MAX_DEPTH:
        raise DomainValidationError(f"{field_name} 的 JSON 嵌套深度不得超过 {JSON_MAX_DEPTH} 层")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise DomainValidationError(f"{field_name} 的 JSON 键必须是字符串")
        count = len(value)
        for item in value.values():
            count += _validate_json_value(item, field_name, depth=depth + 1)
        return count
    if isinstance(value, list | tuple):
        return sum(_validate_json_value(item, field_name, depth=depth + 1) for item in value)
    if value is None or isinstance(value, bool | int | str):
        return 0
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainValidationError(f"{field_name} 不允许 NaN 或 Infinity")
        return 0
    raise DomainValidationError(f"{field_name} 包含不支持的 JSON 类型")


def _copy_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_json_value(item) for item in value)
    return value


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
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DomainValidationError(f"{field_name} 必须是有限正数")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise DomainValidationError(f"{field_name} 必须是有限正数")
    return number


def uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    """校验 UUID 元组；是否允许为空由调用方的状态规则决定。"""
    if not isinstance(value, tuple):
        raise DomainValidationError(f"{field_name} 必须是元组")
    for item in value:
        if not isinstance(item, UUID):
            raise DomainValidationError(f"{field_name} 只能包含 UUID")
    return value
