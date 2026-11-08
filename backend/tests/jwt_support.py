"""测试用 JWT 构造工具。

显式标注：仅用于测试构造令牌，不参与生产路径。生产代码只负责校验。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID, uuid4

JWT_SECRET = "ddp-test-signing-secret-0123456789abcdef"  # noqa: S105 - 测试固定密钥
JWT_ISSUER = "ddp-test"
JWT_AUDIENCE = "ddp-api-test"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(
    *,
    secret: str = JWT_SECRET,
    issuer: str = JWT_ISSUER,
    audience: str | list[str] = JWT_AUDIENCE,
    subject: UUID | str | None = None,
    platform_role: str | None = "member",
    expires_in: int = 300,
    issued_at: int | None = None,
    not_before: int | None = None,
    algorithm: str = "HS256",
    omit: tuple[str, ...] = (),
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """构造一枚 HS256 令牌；``omit`` 用于制造缺失声明的负向用例。"""
    now = int(time.time()) if issued_at is None else issued_at
    header: dict[str, Any] = {"alg": algorithm, "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": str(subject or uuid4()),
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
    }
    if platform_role is not None:
        payload["platform_role"] = platform_role
    if not_before is not None:
        payload["nbf"] = not_before
    if extra_claims:
        payload.update(extra_claims)
    for claim in omit:
        payload.pop(claim, None)

    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    if algorithm == "none":
        signature = b""
    else:
        signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def tamper_payload(token: str, updates: dict[str, Any]) -> str:
    """修改 payload 但保留原签名，用于验证签名校验。"""
    header_segment, payload_segment, signature_segment = token.split(".")
    padding = "=" * (-len(payload_segment) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding).decode("utf-8"))
    payload.update(updates)
    new_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{header_segment}.{new_payload}.{signature_segment}"
