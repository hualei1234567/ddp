"""结构化日志。

阶段 0 使用标准库 + JSON formatter，避免过早引入额外依赖。
formatter 对日志参数做截断与敏感字段脱敏，防止 Token、密码或超长
客户端输入原样进入日志；异常堆栈同样截断后输出。

``trace_id``：每条记录都带 ``trace_id``，取自 ``contextvars``（由
``RequestIdMiddleware`` 按请求设置，取值等于 ``request_id``）。这样日志与
响应头/错误体里的 ``X-Request-Id`` 可以直接对齐，业务代码不必逐处传参。

Worker 处理 outbox 事件时没有 HTTP 请求，本模块提供
:func:`trace_from_event_payload` / :func:`bind_trace` 恢复来源请求的
``request_id``（事件 payload 中的 ``request_id`` 由生产侧原样带入）；
没有来源或来源不合法时生成新 ``trace_id``，并用 ``trace_source=generated``
显式标注，绝不把事件 ID 或聚合 ID 伪装成请求 ID。上下文用 token 复位，
不会跨事件泄漏。

请求外的日志（启动、Worker 主循环）没有追踪上下文，``trace_id`` 为 ``null``，
而不是编造一个值。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import uuid4

from app.core.config import get_settings

#: ``X-Request-Id`` / ``trace_id`` 的长度与字符集上限。与请求头校验共用一条规则，
#: 避免 Worker 恢复上下文时接受 HTTP 侧会拒绝的值。
MAX_TRACE_ID_LENGTH: Final = 128

#: 追踪来源标记：来自事件/审计的既有 request_id，或本地生成。
TRACE_SOURCE_EVENT: Final = "event"
TRACE_SOURCE_GENERATED: Final = "generated"

# 当前请求的追踪标识。默认 ``None`` 表示"不在追踪上下文中"。
trace_id_var: ContextVar[str | None] = ContextVar("ddp_trace_id", default=None)
# 追踪来源；HTTP 请求不设置（保持既有日志字段不变），仅 Worker 事件上下文使用。
trace_source_var: ContextVar[str | None] = ContextVar("ddp_trace_source", default=None)

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


def is_safe_trace_id(value: object) -> bool:
    """追踪标识只接受长度受限的可打印 ASCII，避免日志/响应头注入。

    HTTP 中间件与 Worker 事件上下文共用这一条规则：Worker 从 payload 恢复
    的 ``request_id`` 同样来自外部输入，不能因为"不是请求头"就放宽校验。
    """
    if not isinstance(value, str):
        return False
    if not value or len(value) > MAX_TRACE_ID_LENGTH:
        return False
    return all(32 <= ord(char) < 127 for char in value)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """一次事件处理使用的追踪上下文。

    ``source`` 为 :data:`TRACE_SOURCE_EVENT` 表示取自事件/审计的既有
    ``request_id``；:data:`TRACE_SOURCE_GENERATED` 表示来源缺失或非法、
    由 Worker 生成——后者必须显式标注，不能被误读成"来自某次请求"。
    """

    trace_id: str
    source: str

    @property
    def is_generated(self) -> bool:
        return self.source == TRACE_SOURCE_GENERATED


def trace_from_event_payload(payload: Mapping[str, object] | None) -> TraceContext:
    """从 outbox/审计事件 payload 恢复追踪上下文。

    只认 ``request_id`` 字段：它是审计与 outbox 已有的关联列，语义不变。
    来源缺失、类型不对或不符合安全字符集时生成本地 ``trace_id`` 并标注
    ``generated``；绝不拿 ``event_id`` / ``aggregate_id`` 顶替，避免把
    "某条事件"伪装成"某次请求"。
    """
    raw = payload.get("request_id") if isinstance(payload, Mapping) else None
    if is_safe_trace_id(raw):
        return TraceContext(trace_id=str(raw), source=TRACE_SOURCE_EVENT)
    return TraceContext(trace_id=uuid4().hex, source=TRACE_SOURCE_GENERATED)


def current_trace_id() -> str | None:
    """当前上下文的 ``trace_id``（无上下文时为 ``None``）。"""
    return trace_id_var.get()


@contextmanager
def bind_trace(context: TraceContext) -> Iterator[TraceContext]:
    """在 ``with`` 块内绑定追踪上下文，退出时按 token 复位。

    用 token 而非 ``set(None)``：嵌套（如 Worker 调用的应用服务内部再次
    绑定）或异常退出时不会丢失外层值；事件之间也不会串号。
    """
    id_token = trace_id_var.set(context.trace_id)
    source_token = trace_source_var.set(context.source)
    try:
        yield context
    finally:
        trace_source_var.reset(source_token)
        trace_id_var.reset(id_token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "message": _format_message(record),
        }
        source = trace_source_var.get()
        if source is not None:
            # 只在 Worker 事件上下文输出；HTTP 请求日志保持既有字段不变。
            payload["trace_source"] = source
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
