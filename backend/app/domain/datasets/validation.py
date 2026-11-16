"""数据集领域值校验辅助。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import canonical_json, plain, validate_sha256
from app.domain.datasets.errors import DomainValidationError

# JSON 载荷上限：防止把不受控大对象写进 JSON 列。报告类载荷远小于此值。
JSON_PAYLOAD_MAX_BYTES = 64 * 1024


def text_value(value: object, field_name: str, *, max_length: int) -> str:
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
    return text_value(value, field_name, max_length=max_length)


def required_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise DomainValidationError(f"{field_name} 必须是 UUID")
    return value


def optional_uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    return required_uuid(value, field_name)


def non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} 必须是非负整数")
    return value


def positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DomainValidationError(f"{field_name} 必须是正整数")
    return value


def aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须是带时区时间")
    return value


def optional_aware_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return aware_datetime(value, field_name)


def sha256_value(value: object, field_name: str) -> str:
    try:
        return validate_sha256(value, field_name)
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc


def optional_sha256(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return sha256_value(value, field_name)


def enum_list(value: object, enum_type: type, field_name: str) -> tuple:
    if not isinstance(value, list | tuple) or not value:
        raise DomainValidationError(f"{field_name} 必须是非空列表")
    result = []
    for item in value:
        try:
            parsed = item if isinstance(item, enum_type) else enum_type(item)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 包含非法取值") from exc
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


def optional_enum_list(value: object, enum_type: type, field_name: str) -> tuple:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise DomainValidationError(f"{field_name} 必须是列表")
    result = []
    for item in value:
        try:
            parsed = item if isinstance(item, enum_type) else enum_type(item)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 包含非法取值") from exc
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


def coerce_enum(value: object, enum_type: type, field_name: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} 取值非法") from exc


def optional_payload(value: object, field_name: str) -> dict[str, object] | None:
    """校验可选 JSON 对象载荷：必须是 Mapping、可规范化且不超过上限。

    ``None`` 保留"未记录"语义，不得被折叠成 ``{}``——两者的含义不同
    （未核对 vs 核对无内容）。
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{field_name} 必须是对象")
    normalized = plain(dict(value))
    if not isinstance(normalized, dict):
        raise DomainValidationError(f"{field_name} 必须是对象")
    try:
        encoded = canonical_json(normalized)
    except TypeError as exc:
        raise DomainValidationError(f"{field_name} 必须是可规范化 JSON") from exc
    if len(encoded.encode("utf-8")) > JSON_PAYLOAD_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 超过最大长度 {JSON_PAYLOAD_MAX_BYTES} 字节")
    return normalized
