"""结构化日志。

阶段 0 使用标准库 + JSON formatter，避免过早引入额外依赖。
formatter 对日志参数做截断与敏感字段脱敏，防止 Token、密码或超长
客户端输入原样进入日志；异常堆栈同样截断后输出。

``trace_id``：每条记录都带 ``trace_id``，取自 ``contextvars``（由
``RequestIdMiddleware`` 按请求设置，取值等于 ``request_id``）。这样日志与
响应头/错误体里的 ``X-Request-Id`` 可以直接对齐，业务代码不必逐处传参。

``otel_trace_id``：**仅在 OpenTelemetry 追踪开启时出现**的分布式追踪 ID，
与响应头 ``X-Trace-Id``、链路后端里的 span 同值。两者刻意不合并：

- ``trace_id`` 是既有的请求关联 ID（= ``request_id``），它被写进审计记录与
  outbox 事件的 ``request_id`` 字段，"请求 → 事件 → 审计"这条链依赖它的
  稳定性，不能随追踪开关变化；
- ``otel_trace_id`` 是 OTel 的链路 ID，只在追踪开启时有意义。

若把两者合成一个字段，同一个键会在不同配置下表示不同的东西（口径漂移），
也让审计里的 ``request_id`` 在开启追踪后不再等于请求的 ``request_id``。
因此：追踪关闭时日志字段集合与接入前**完全一致**（不出现 ``otel_trace_id``），
开启时多一个显式命名的字段。

Worker 处理 outbox 事件时没有 HTTP 请求，本模块提供
:func:`trace_from_event_payload` / :func:`bind_trace` 恢复来源请求的
``request_id``（事件 payload 中的 ``request_id`` 由生产侧原样带入）；
没有来源或来源不合法时生成新 ``trace_id``，并用 ``trace_source=generated``
显式标注，绝不把事件 ID 或聚合 ID 伪装成请求 ID。上下文用 token 复位，
不会跨事件泄漏。

请求外的日志（启动、Worker 主循环）没有追踪上下文，``trace_id`` 为 ``null``，
而不是编造一个值。

脱敏规则只在本模块实现一份：日志格式化用 :func:`redact_credentials`，导入/处理
步骤日志等对外回显文本用 :func:`sanitize_log_message`（在此之上再加协议地址与
绝对路径清洗）。``application.observability_redaction`` 只是兼容既有导入路径的
薄适配，不得另起一套正则。
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

#: 对外可见文本（步骤日志、失败摘要）的默认列宽；调用方可按列宽覆盖。
DEFAULT_LOG_MESSAGE_MAX_LENGTH: Final = 1000

#: 对外回显文本使用的凭据占位符。规则本体与日志格式化完全相同，只是展示风格
#: 不同（日志用 ``***``，回给前端的步骤日志用中文占位符）。
_EXTERNAL_CREDENTIAL_PLACEHOLDER: Final = "[已脱敏凭据]"

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

# 文本里的敏感键名。允许 ``access_token`` / ``refresh-token`` / ``x-api-key`` /
# ``client_secret`` / ``db_password`` / ``aws_access_key_id`` / ``secret key``
# 这类多段复合键；后缀只放行少量通用段，避免把 ``token_count=5`` 这类计数
# 指标误判为凭据——过度脱敏会让日志失去排障价值。
_SENSITIVE_KEY_NAME = (
    r"(?:[A-Za-z0-9]+[._ -])*"
    r"(?:"
    r"password|passwd|secret|token|authorization|cookie|credential|credentials"
    r"|api[._ -]?key|apikey"
    r"|(?:access|refresh|client|session|private|signing|secret)[._ -]?key"
    r"|(?:access|refresh|client|session)[._ -]?token"
    r"|client[._ -]?secret"
    r")"
    r"(?:[._ -](?:id|key|token|secret|value|hash|file|path))?"
)

# 键名 + 分隔符 + 可选 Bearer scheme。JSON 形态 ``"api_key": "..."`` 的收尾
# 引号也在 prefix 内，替换后引号结构保持不变。
_CREDENTIAL_PREFIX = (
    r"(?P<prefix>(?<![A-Za-z0-9_])" + _SENSITIVE_KEY_NAME + r"[\"']?\s*[=:]\s*(?:bearer\s+)?)"
)

# 引号值优先匹配：``password="a b"`` 若先走非引号规则，只会吃掉 ``"a``，
# 把 ``b"`` 留在日志里。引号内允许空格和另一种引号字符。
_QUOTED_CREDENTIAL_PATTERN = re.compile(
    rf"(?i){_CREDENTIAL_PREFIX}(?P<quote>['\"])(?:(?!(?P=quote)).)*?(?P=quote)"
)
# 非引号值到空白/逗号/分号/引号为止，不越过行内分隔符。
_UNQUOTED_CREDENTIAL_PATTERN = re.compile(rf"(?i){_CREDENTIAL_PREFIX}(?!['\"])([^\s,;&\"']+)")

# Pydantic 的校验错误把用户输入原样带进文本：
#   ``String should have at least 20 characters [type=string_too_short,
#   input_value='hunter2', input_type=str]``
# 键名本身不敏感，固定格式后面紧跟的引号值必须整体抹掉。
_PYDANTIC_INPUT_PATTERN = re.compile(
    r"(?i)(?P<prefix>\binput_value\s*[=:]\s*)" r"(?P<quote>['\"])(?:(?!(?P=quote)).)*?(?P=quote)"
)
# 裸 Bearer：没有键名时也要抹掉 scheme 后面的令牌。
_BEARER_PATTERN = re.compile(r"(?i)(?P<prefix>\b(?:bearer\s+))([A-Za-z0-9._~+/=-]+)")

# 对外回显文本额外要求：协议地址、预签名/凭证查询参数、绝对路径不得外发。
_URL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
# 预签名/凭证查询参数；URL 整体被替换后通常已不存在，单独处理防漏网。
_CREDENTIAL_QUERY_PATTERN = re.compile(
    r"(?i)\b(X-Amz-[A-Za-z]+|Signature|Credential|access_token|sig)=[^&\s]+"
)
# 绝对路径：POSIX（以 / 开头）与 Windows（C:\ 开头）。前置字符不能是字母
# 数字或点，避免把 "3/10"、"and/or" 这类普通文本误伤。
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![\w.~])(?:/(?=[^\s\"'<>])[^\s\"'<>]*|[A-Za-z]:\\[^\s\"'<>]*)"
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum] + "…<truncated>"


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


# 键名判定：``token`` 子串会把 ``tokenizer`` 误判为凭据，因此公开入口用
# 边界正则；日志参数的宽松子串判定仍保留在 ``_is_sensitive_key`` 内部。
_SECRET_NAME_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:token|secret|password|passwd|api[_-]?key|apikey|authorization|credential|"
    r"cookie|private[_-]?key)"
    r"(?:$|[^a-z0-9])"
)


def is_secret_name(name: object) -> bool:
    """键名/字段名是否表示凭据类信息。

    ``hf_token`` 命中，``tokenizer``/``token_count`` 不命中。只做名字判断，
    不读取也不推断值；凭据值仍由 :func:`redact_credentials` 处理。
    """
    return bool(_SECRET_NAME_PATTERN.search(str(name or "")))


def _redaction_replacement(match: re.Match[str], placeholder: str, keep_key: bool) -> str:
    """替换策略：默认保留键名与分隔符（日志可读），对外文本整体替换。"""
    if not keep_key:
        return placeholder
    quote = match.groupdict().get("quote") or ""
    # 引号要成对保留：开引号在 quote 分组里，不在 prefix 内。
    return f"{match.group('prefix')}{quote}{placeholder}{quote}"


def redact_credentials(
    text: str,
    *,
    placeholder: str = "***",
    keep_key: bool = True,
) -> str:
    """抹掉文本里的凭据；日志格式化与对外回显共用这一份规则。

    覆盖：Bearer 令牌、``key=value`` / ``key: value``（含多段复合键名）、
    单/双引号包裹且含空格的值、Pydantic ``input_value`` 固定格式。

    顺序有讲究：引号值先于非引号值，否则 ``password="a b"`` 会被非引号规则
    只吃掉 ``"a``。已写入的占位符不会被后续规则二次改写。
    """
    if not text:
        return text
    result = _QUOTED_CREDENTIAL_PATTERN.sub(
        lambda match: _redaction_replacement(match, placeholder, keep_key), text
    )
    result = _UNQUOTED_CREDENTIAL_PATTERN.sub(
        lambda match: _redaction_replacement(match, placeholder, keep_key), result
    )
    result = _PYDANTIC_INPUT_PATTERN.sub(
        lambda match: _redaction_replacement(match, placeholder, keep_key), result
    )
    return _BEARER_PATTERN.sub(
        lambda match: _redaction_replacement(match, placeholder, keep_key), result
    )


def sanitize_log_message(
    message: object,
    *,
    limit: int = DEFAULT_LOG_MESSAGE_MAX_LENGTH,
) -> str:
    """把任意异常/日志文本压成可安全外发的一行。

    - 协议地址（http/https/s3/hf 等）→ ``[已脱敏地址]``；
    - 绝对路径（``/...``、``C:\\...``）→ ``[已脱敏路径]``；
    - 凭据形态（``token=...``、``Bearer ...``、引号包裹的值等）→
      ``[已脱敏凭据]``；
    - 所有空白折叠为单个空格，并按列宽截断。

    永不返回空串：脱敏后为空时返回 ``"(无更多信息)"``，避免前端出现空白行。
    """
    text = _WHITESPACE_PATTERN.sub(" ", str(message or "")).strip()
    text = _URL_PATTERN.sub("[已脱敏地址]", text)
    text = _CREDENTIAL_QUERY_PATTERN.sub(_EXTERNAL_CREDENTIAL_PLACEHOLDER, text)
    text = _ABSOLUTE_PATH_PATTERN.sub("[已脱敏路径]", text)
    text = redact_credentials(text, placeholder=_EXTERNAL_CREDENTIAL_PLACEHOLDER, keep_key=False)
    if not text:
        return "(无更多信息)"
    return text[:limit]


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
        return _truncate(redact_credentials(value), _MAX_LOG_ARG_LENGTH)
    if value is None or isinstance(value, bool | int | float):
        return value
    return _truncate(redact_credentials(str(value)), _MAX_LOG_ARG_LENGTH)


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
    return _truncate(redact_credentials(text), _MAX_LOG_MESSAGE_LENGTH)


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
        otel_trace_id = _otel_trace_id()
        if otel_trace_id is not None:
            # 追踪关闭时**不出现**这个键：日志字段集合与接入前完全一致。
            payload["otel_trace_id"] = otel_trace_id
        if record.exc_info:
            payload["exc"] = _truncate(
                redact_credentials(self.formatException(record.exc_info)),
                _MAX_LOG_EXCEPTION_LENGTH,
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


def _otel_trace_id() -> str | None:
    """当前 OTel trace id；追踪未启用或当前无 span 时为 ``None``。

    这里**不能** import ``app.core.telemetry`` 到模块顶层：追踪关闭时
    OTel 模块根本不该被 import（硬约束：零开销）。故走函数内延迟 import。
    """
    from app.core.telemetry import current_trace_id as otel_current_trace_id

    return otel_current_trace_id()


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
