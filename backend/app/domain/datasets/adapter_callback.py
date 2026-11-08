"""训练 Adapter 回调验签与能力校验（纯函数，无 Web/DB 依赖）。

签名算法：HMAC-SHA256(secret, f"{timestamp}.{nonce}.{raw_body}")，
以 ``sha256=<hex>``（兼容 ``sha256:<hex>``）传输；时间窗默认 ±300 秒。
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from datetime import datetime

from app.domain.datasets.training_enums import AdapterCapability
from app.domain.datasets.training_errors import (
    TrainingAdapterUnsupportedError,
    TrainingCallbackInvalidError,
)

DEFAULT_TIME_WINDOW_SECONDS = 300
_MIN_NONCE_LENGTH = 8
_MAX_NONCE_LENGTH = 128


def compute_callback_signature(
    secret: str,
    timestamp: str | int,
    nonce: str,
    body: bytes,
) -> str:
    message = f"{timestamp}.{nonce}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _normalized_signature(value: str) -> str:
    raw = value.strip()
    lowered = raw.lower()
    if lowered.startswith("sha256=") or lowered.startswith("sha256:"):
        raw = raw[7:]
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        raise TrainingCallbackInvalidError("回调签名格式非法")
    return raw.lower()


def validate_nonce(nonce: str) -> str:
    if not isinstance(nonce, str):
        raise TrainingCallbackInvalidError("回调 nonce 非法")
    value = nonce.strip()
    if len(value) < _MIN_NONCE_LENGTH or len(value) > _MAX_NONCE_LENGTH:
        raise TrainingCallbackInvalidError("回调 nonce 长度非法")
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
    """验签、校验时间窗与 nonce；返回规范化 nonce。失败统一 401。"""
    if not secret:
        raise TrainingCallbackInvalidError("Adapter 回调密钥未配置")
    if timestamp is None or signature is None or nonce is None:
        raise TrainingCallbackInvalidError("缺少回调签名头")
    try:
        timestamp_seconds = int(timestamp.strip())
    except (TypeError, ValueError) as exc:
        raise TrainingCallbackInvalidError("回调时间戳非法") from exc
    if timestamp_seconds < 0:
        raise TrainingCallbackInvalidError("回调时间戳非法")
    if window_seconds < 1:
        raise TrainingCallbackInvalidError("回调时间窗配置非法")
    drift = abs(now.timestamp() - timestamp_seconds)
    if drift > window_seconds:
        raise TrainingCallbackInvalidError("回调时间戳超出允许窗口")
    normalized_nonce = validate_nonce(nonce)
    expected = _normalized_signature(
        compute_callback_signature(secret, timestamp_seconds, normalized_nonce, body)
    )
    actual = _normalized_signature(signature)
    if not hmac.compare_digest(expected, actual):
        raise TrainingCallbackInvalidError("回调签名校验失败")
    return normalized_nonce


def ensure_capabilities(
    capabilities: Iterable[str],
    required: AdapterCapability,
    *,
    adapter_id: str,
) -> None:
    supported = {item.value if hasattr(item, "value") else str(item) for item in capabilities}
    if required.value not in supported:
        raise TrainingAdapterUnsupportedError(
            f"Adapter {adapter_id} 不支持能力 {required.value}"
        )


__all__ = [
    "DEFAULT_TIME_WINDOW_SECONDS",
    "compute_callback_signature",
    "ensure_capabilities",
    "validate_nonce",
    "verify_callback_signature",
]
