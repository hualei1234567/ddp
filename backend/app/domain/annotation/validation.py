"""标注域字段校验。

只覆盖标注域自身规则，不提供跨模块通用工具；不依赖 Web/ORM/队列。
错误文案只包含字段名与固定规则，不拼接客户端原始值。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import TypeVar
from uuid import UUID

from app.domain.annotation.errors import DomainValidationError

EnumT = TypeVar("EnumT", bound=Enum)

CODE_MAX_LENGTH = 64
NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
REASON_MAX_LENGTH = 1000
LABEL_KEY_MAX_LENGTH = 100
VERSION_TEXT_MAX_LENGTH = 64
JSON_MAX_BYTES = 256 * 1024
JSON_MAX_KEYS = 2048
JSON_MAX_DEPTH = 12
SHA256_PREFIX = "sha256:"


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


def non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    """校验 UUID 序列并去重；拒绝字符串 UUID，避免绕过类型约束。"""
    if value is None:
        return ()
    if not isinstance(value, list | tuple | set | frozenset):
        raise DomainValidationError(f"{field_name} 必须是 UUID 列表")
    result: list[UUID] = []
    seen: set[UUID] = set()
    for item in value:
        if not isinstance(item, UUID):
            raise DomainValidationError(f"{field_name} 只能包含 UUID")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def required_uuid(value: object, field_name: str) -> UUID:
    result = optional_uuid(value, field_name)
    if result is None:
        raise DomainValidationError(f"{field_name} 不能为空")
    return result


def aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须带时区（UTC）")
    return value


def optional_aware_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return aware_datetime(value, field_name)


def check_time_range(start: datetime | None, end: datetime | None, field_prefix: str) -> None:
    if start is not None and end is not None and end < start:
        raise DomainValidationError(f"{field_prefix}_end 不得早于 {field_prefix}_start")


def check_ns_range(start: int | None, end: int | None, field_prefix: str) -> None:
    if (start is None) != (end is None):
        raise DomainValidationError(f"{field_prefix}_start 与 {field_prefix}_end 必须同时提供")
    if start is None or end is None:
        return
    if not isinstance(start, int) or isinstance(start, bool):
        raise DomainValidationError(f"{field_prefix}_start_ns 必须是整数")
    if not isinstance(end, int) or isinstance(end, bool):
        raise DomainValidationError(f"{field_prefix}_end_ns 必须是整数")
    if start >= end:
        raise DomainValidationError(f"{field_prefix}_start_ns 必须小于 {field_prefix}_end_ns")


def validate_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是 sha256 字符串")
    text = value.strip().lower()
    digest = text.removeprefix(SHA256_PREFIX)
    if (
        not text.startswith(SHA256_PREFIX)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise DomainValidationError(f"{field_name} 必须形如 sha256:<64 位小写十六进制>")
    return f"{SHA256_PREFIX}{digest}"


def frozen_mapping(value: object, field_name: str) -> Mapping[str, object]:
    """校验 JSON 对象并冻结顶层，避免调用方修改穿透领域实体。"""
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    key_count = _validate_json_value(value, field_name, depth=0)
    if key_count > JSON_MAX_KEYS:
        raise DomainValidationError(f"{field_name} 的对象键总数不得超过 {JSON_MAX_KEYS} 个")
    copied = _copy_json_value(value)
    encoded = canonical_json(copied)
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后不得超过 {JSON_MAX_BYTES} 字节")
    return MappingProxyType(copied)


def frozen_sequence(value: object, field_name: str) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field_name} 必须是对象列表")
    return tuple(frozen_mapping(item, field_name) for item in value)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("值无法序列化为稳定 JSON") from exc


def canonical_sha256(value: object) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return f"{SHA256_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"不支持的 JSON 类型：{type(value).__name__}")


def _validate_json_value(value: object, field_name: str, *, depth: int) -> int:
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
    if isinstance(value, UUID | Enum):
        return 0
    raise DomainValidationError(f"{field_name} 包含不支持的 JSON 类型")


def _copy_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _copy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_json_value(item) for item in value]
    return value
