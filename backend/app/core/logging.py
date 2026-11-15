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
# Pydantic 的校验错误把用户输入原样带进文本：
#   ``String should have at least 20 characters [type=string_too_short,
#   input_value='hunter2', input_type=str]``
# 形如 ``input_value=`` 的键名本身不敏感，真正的值是紧跟在它后面的引号串，
# 上一行的 ``_ASSIGNMENT_PATTERN`` 只吃 ``\S+``（到空格为止），对带引号且
# 含空格的输入会漏。因此单独匹配"敏感键名 + 引号包裹的值"两种形态：
#   1. ``password='...'`` / ``"api_key": "..."``（敏感键名紧跟引号值）
#   2. ``input_value='...'``（Pydantic 固定格式，值必须整体抹掉）
# 键名两侧可能带引号（JSON 形态 ``"api_key": "sk-..."``），因此键名表达式
# 必须把可选的引号算进匹配范围，否则 ``"api_key"`` 与冒号之间的引号会挡住匹配。
_QUOTED_SECRET_PATTERN = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)"
    r"[\"']?\s*[=:]\s*)(['\"])[^'\"]*\2"
)
_PYDANTIC_INPUT_PATTERN = re.compile(r"(?i)(\binput_value\s*[=:]\s*)(['\"])[^'\"]*\2")


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum] + "…<truncated>"


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_text(value: str) -> str:
    """抹掉文本里的凭据。

    覆盖四类形态，顺序有讲究：引号形态先处理，否则 ``password="a b"`` 会被
    ``_ASSIGNMENT_PATTERN`` 只吃掉 ``"a``，留下 ``b"`` 在日志里。
    """
    redacted = _BEARER_PATTERN.sub(r"\1***", value)
    redacted = _QUOTED_SECRET_PATTERN.sub(r"\1\2***\2", redacted)
    redacted = _PYDANTIC_INPUT_PATTERN.sub(r"\1\2***\2", redacted)
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
