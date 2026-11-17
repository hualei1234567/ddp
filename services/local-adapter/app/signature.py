"""DDP 回调签名：HMAC-SHA256(secret, f"{timestamp}.{nonce}." + raw_body)。"""

from __future__ import annotations

import hashlib
import hmac
import time
from uuid import uuid4

MIN_NONCE_LENGTH = 8
MAX_NONCE_LENGTH = 128


def compute_signature(
    secret: str, timestamp: int | str, nonce: str, body: bytes
) -> str:
    message = f"{timestamp}.{nonce}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def callback_headers(
    secret: str,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """每次发送都生成新的时间戳与 nonce；body 必须保持原始字节不变。"""
    if not secret:
        raise ValueError("回调共享密钥为空")
    moment = int(time.time()) if timestamp is None else int(timestamp)
    value = uuid4().hex if nonce is None else nonce
    if not MIN_NONCE_LENGTH <= len(value) <= MAX_NONCE_LENGTH:
        raise ValueError("nonce 长度必须在 8-128 之间")
    return {
        "Content-Type": "application/json",
        "X-DDP-Signature": compute_signature(secret, moment, value, body),
        "X-DDP-Timestamp": str(moment),
        "X-DDP-Nonce": value,
    }


__all__ = [
    "MAX_NONCE_LENGTH",
    "MIN_NONCE_LENGTH",
    "callback_headers",
    "compute_signature",
]
