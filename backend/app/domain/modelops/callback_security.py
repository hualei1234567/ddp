"""模型 Adapter 回调验签（纯函数，无 Web/DB 依赖）。"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from app.domain.modelops.errors import ModelCallbackInvalidError

DEFAULT_TIME_WINDOW_SECONDS = 300
_MIN_NONCE_LENGTH = 8
_MAX_NONCE_LENGTH = 128


def compute_signature(secret: str, timestamp: str | int, nonce: str, body: bytes) -> str:
    message = f"{timestamp}.{nonce}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _normalized(value: str) -> str:
    raw = value.strip()
    lowered = raw.lower()
    if lowered.startswith("sha256=") or lowered.startswith("sha256:"):
        raw = raw[7:]
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        raise ModelCallbackInvalidError("回调签名格式非法")
    return raw.lower()


def validate_nonce(nonce: str) -> str:
    value = nonce.strip()
    if len(value) < _MIN_NONCE_LENGTH or len(value) > _MAX_NONCE_LENGTH:
        raise ModelCallbackInvalidError("回调 nonce 长度非法")
    return value


def verify_callback_signature(
    *,
    secret: str | None,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    body: bytes,
    now: datetime,
    window_seconds: int = DEFAULT_TIME_WINDOW_SECONDS,
) -> str:
    if not secret:
        raise ModelCallbackInvalidError("Adapter 回调密钥未配置")
    if timestamp is None or signature is None or nonce is None:
        raise ModelCallbackInvalidError("缺少回调签名头")
    try:
        seconds = int(timestamp.strip())
    except (TypeError, ValueError) as exc:
        raise ModelCallbackInvalidError("回调时间戳非法") from exc
    if seconds < 0:
        raise ModelCallbackInvalidError("回调时间戳非法")
    if window_seconds < 1:
        raise ModelCallbackInvalidError("回调时间窗配置非法")
    if abs(now.timestamp() - seconds) > window_seconds:
        raise ModelCallbackInvalidError("回调时间戳超出允许窗口")
    normalized_nonce = validate_nonce(nonce)
    expected = _normalized(compute_signature(secret, seconds, normalized_nonce, body))
    actual = _normalized(signature)
    if not hmac.compare_digest(expected, actual):
        raise ModelCallbackInvalidError("回调签名校验失败")
    return normalized_nonce
