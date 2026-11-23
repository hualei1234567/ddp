"""日志与诊断脱敏工具。

安全红线（ADR-0047 / FR-001）：配对凭证、Agent Token、JWT、Bearer、预签名签名参数、
私钥等任何 secret 严禁出现在 Manifest、日志或诊断输出中。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "pairing_code",
    "pairing_token",
    "bearer",
    "cookie",
    "signature",
)

_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)(x-amz-signature|x-amz-credential|x-amz-security-token)=[^&\s]+"),
    re.compile(
        r"(?i)(agent_token|pairing_token|pairing_code|api_key|secret|password)\s*[=:]\s*\S+"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(text: str, known_secrets: Iterable[str] = ()) -> str:
    """对字符串中的已知 secret 及常见凭证模式执行脱敏。"""
    result = text
    for secret in known_secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def redact_mapping(data: Mapping[str, Any], known_secrets: Iterable[str] = ()) -> dict[str, Any]:
    """递归脱敏字典：命中敏感键名或敏感值模式均替换为 ``[REDACTED]``。"""
    secrets = tuple(s for s in known_secrets if s)
    return _redact_node(data, secrets)


def _redact_node(node: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(node, Mapping):
        out: dict[str, Any] = {}
        for key, value in node.items():
            key_str = str(key)
            if any(frag in key_str.lower() for frag in _SECRET_KEY_FRAGMENTS):
                out[key_str] = REDACTED
            else:
                out[key_str] = _redact_node(value, secrets)
        return out
    if isinstance(node, str):
        return redact_text(node, secrets)
    if isinstance(node, list | tuple):
        cleaned = [_redact_node(item, secrets) for item in node]
        return tuple(cleaned) if isinstance(node, tuple) else cleaned
    return node


class SecretRedactingFilter(logging.Filter):
    """标准库 logging.Filter：确保记录输出不含已知 secret 或凭证模式。"""

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: set[str] = {s for s in known_secrets if s}

    def register_secret(self, secret: str) -> None:
        if secret:
            self._secrets.add(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = redact_text(rendered, self._secrets)
        record.args = ()
        return True
