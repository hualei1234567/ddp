"""ExportJob 字段校验辅助。

只包含纯 Python 规则；配置 Hash 由服务端规范化计算，客户端提交的 Hash 不作为权威。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.domain.datasets.canonical import plain
from app.domain.datasets.enums import ExportFormat
from app.domain.datasets.errors import (
    DomainValidationError,
    ExportConfigInvalidError,
    ExportFormatUnsupportedError,
)
from app.domain.datasets.validation import non_negative_int, text_value

EXPORT_FORMAT_VERSION_LE_ROBOT = "ddp.lerobot/1.0.0"
IDEMPOTENCY_KEY_MIN_LENGTH = 8
IDEMPOTENCY_KEY_MAX_LENGTH = 128
CONFIG_MAX_BYTES = 64 * 1024
_SECRET_KEY_TOKENS = ("token", "secret", "password", "credential", "api_key", "apikey")


def config_payload(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ExportConfigInvalidError("config 必须是 JSON 对象")
    payload = {str(key): plain(item) for key, item in value.items()}
    for key in payload:
        lowered = key.lower()
        if any(token in lowered for token in _SECRET_KEY_TOKENS):
            raise ExportConfigInvalidError("config 不得包含凭据类字段")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExportConfigInvalidError("config 必须是合法 JSON 对象") from exc
    if len(encoded) > CONFIG_MAX_BYTES:
        raise ExportConfigInvalidError("config 序列化后超过大小上限")
    return payload


def safe_output_prefix(value: object) -> str:
    text = text_value(value, "output_prefix", max_length=1024)
    if text.startswith("/") or ".." in text.split("/") or "\\" in text:
        raise DomainValidationError("output_prefix 非法")
    if not all(char.isalnum() or char in "._/-" for char in text):
        raise DomainValidationError("output_prefix 只能包含安全路径字符")
    return text


def optional_non_negative(value: object, field_name: str) -> int | None:
    return None if value is None else non_negative_int(value, field_name)


def coerce_format(value: object) -> ExportFormat:
    try:
        return value if isinstance(value, ExportFormat) else ExportFormat(value)
    except ValueError as exc:
        raise ExportFormatUnsupportedError("仅支持 LE_ROBOT 导出格式") from exc


def text_limit(field_name: str) -> int:
    if field_name == "error_code":
        return 100
    return 1024


__all__ = [
    "CONFIG_MAX_BYTES",
    "EXPORT_FORMAT_VERSION_LE_ROBOT",
    "IDEMPOTENCY_KEY_MAX_LENGTH",
    "IDEMPOTENCY_KEY_MIN_LENGTH",
    "coerce_format",
    "config_payload",
    "optional_non_negative",
    "safe_output_prefix",
    "text_limit",
]
