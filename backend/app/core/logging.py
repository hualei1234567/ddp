"""结构化日志。

阶段 0 使用标准库 + JSON formatter，避免过早引入额外依赖。
formatter 对日志参数做截断与敏感字段脱敏，防止 Token、密码或超长
客户端输入原样进入日志；异常堆栈同样截断后输出。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings

_MAX_LOG_ARG_LENGTH = 200
_MAX_LOG_MESSAGE_LENGTH = 2000
_MAX_LOG_EXCEPTION_LENGTH = 4000
_MAX_LOG_ITEMS = 20
_MAX_LOG_DEPTH = 4

_SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "credential",
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)" r"\s*[=:]\s*\S+"
)


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum] + "…<truncated>"


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub(r"\1***", value)
    return _ASSIGNMENT_PATTERN.sub(r"\1=***", redacted)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_LOG_DEPTH:
        return "***"
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_LOG_ITEMS:
                safe["..."] = f"<{len(value) - _MAX_LOG_ITEMS} more keys>"
                break
            safe[str(key)] = "***" if _is_sensitive_key(key) else _safe_value(item, depth=depth + 1)
        return safe
    if isinstance(value, list | tuple):
        items = [_safe_value(item, depth=depth + 1) for item in value[:_MAX_LOG_ITEMS]]
        if len(value) > _MAX_LOG_ITEMS:
            items.append(f"<{len(value) - _MAX_LOG_ITEMS} more items>")
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
        return _truncate(_redact_text(value), _MAX_LOG_ARG_LENGTH)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _truncate(_redact_text(str(value)), _MAX_LOG_ARG_LENGTH)


def _safe_args(args: object) -> object:
    if isinstance(args, Mapping):
        return {
            key: "***" if _is_sensitive_key(key) else _safe_value(item)
            for key, item in args.items()
        }
    if isinstance(args, tuple):
        return tuple(_safe_value(item) for item in args)
    return _safe_value(args)


def _format_message(record: logging.LogRecord) -> str:
    raw = record.msg
    if not record.args:
        text = str(raw)
    else:
        safe_args = _safe_args(record.args)
        try:
            text = str(raw) % safe_args
        except (KeyError, TypeError, ValueError):
            # 格式串与参数不匹配时绝不抛异常，退化为安全拼接。
            text = f"{raw} {safe_args}"
    return _truncate(_redact_text(text), _MAX_LOG_MESSAGE_LENGTH)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": _format_message(record),
        }
        if record.exc_info:
            payload["exc"] = _truncate(
                _redact_text(self.formatException(record.exc_info)),
                _MAX_LOG_EXCEPTION_LENGTH,
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
