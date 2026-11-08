"""HS256 JWT 校验适配器。

使用标准库实现，避免为一次 HS256 校验引入第三方依赖。安全约束：

- 只接受 ``HS256``，拒绝 ``none``、非对称算法与未处理的 ``crit`` 扩展；
- 严格校验签名、``exp``、``nbf``、``iss``、``aud``；
- 所有失败都抛出 :class:`TokenValidationError`，由 API 层统一映射为 401，
  不向客户端区分“签名错误”“已过期”等内部原因。

本模块只做协议解析与校验，不依赖 FastAPI、SQLAlchemy 或业务领域模型。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from typing import Any

_ALGORITHM = "HS256"
_MAX_TOKEN_LENGTH = 8192
_MAX_SUBJECT_LENGTH = 512


class TokenValidationError(Exception):
    """访问令牌不合法；调用方必须统一映射为 401。"""


@dataclass(frozen=True, slots=True)
class JwtClaims:
    """通过签名与时间校验后的最小声明集合。"""

    subject: str
    platform_role: str


def decode_hs256_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    leeway_seconds: int = 0,
    now: float | None = None,
) -> JwtClaims:
    """校验 HS256 JWT 并返回主体声明。

    ``now`` 仅用于测试注入固定时钟；生产调用不传，使用系统时间。
    """
    if not isinstance(token, str) or not token:
        raise TokenValidationError("令牌为空")
    if len(token) > _MAX_TOKEN_LENGTH:
        raise TokenValidationError("令牌超长")
    if not token.isascii():
        raise TokenValidationError("令牌包含非 ASCII 字符")
    # 密钥强度由 ``Settings.validate_runtime_security`` 在非本地环境启动时强制；
    # 适配器本身只要求密钥存在，便于 local 环境使用开发默认值。
    if not secret:
        raise TokenValidationError("签名密钥未配置")
    if not issuer or not audience:
        raise TokenValidationError("issuer 或 audience 未配置")
    if leeway_seconds < 0:
        raise TokenValidationError("时钟容差不能为负")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenValidationError("令牌格式错误")
    header_b64, payload_b64, signature_b64 = parts
    if not header_b64 or not payload_b64 or not signature_b64:
        raise TokenValidationError("令牌格式错误")

    header = _decode_json_object(header_b64)
    if header.get("alg") != _ALGORITHM:
        raise TokenValidationError("不支持的签名算法")
    if "crit" in header:
        raise TokenValidationError("不支持的 crit 扩展")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(signature_b64)
    if len(actual) != len(expected) or not hmac.compare_digest(actual, expected):
        raise TokenValidationError("签名校验失败")

    payload = _decode_json_object(payload_b64)
    current_time = time.time() if now is None else now
    if not _is_timestamp(current_time):
        raise TokenValidationError("时钟无效")

    _validate_expiry(payload, current_time, leeway_seconds)
    _validate_not_before(payload, current_time, leeway_seconds)
    _validate_issuer(payload, issuer)
    _validate_audience(payload, audience)

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > _MAX_SUBJECT_LENGTH:
        raise TokenValidationError("sub 声明无效")

    platform_role = payload.get("platform_role", "member")
    if not isinstance(platform_role, str) or not platform_role.strip():
        raise TokenValidationError("platform_role 声明无效")

    return JwtClaims(
        subject=subject.strip(),
        platform_role=platform_role.strip(),
    )


def _validate_expiry(
    payload: dict[str, Any],
    now: float,
    leeway_seconds: int,
) -> None:
    expires_at = payload.get("exp")
    if not _is_timestamp(expires_at):
        raise TokenValidationError("缺少有效的 exp 声明")
    if now > expires_at + leeway_seconds:
        raise TokenValidationError("令牌已过期")


def _validate_not_before(
    payload: dict[str, Any],
    now: float,
    leeway_seconds: int,
) -> None:
    if "nbf" not in payload:
        return
    not_before = payload["nbf"]
    if not _is_timestamp(not_before):
        raise TokenValidationError("nbf 声明无效")
    if now + leeway_seconds < not_before:
        raise TokenValidationError("令牌尚未生效")


def _validate_issuer(payload: dict[str, Any], issuer: str) -> None:
    if payload.get("iss") != issuer:
        raise TokenValidationError("iss 声明不匹配")


def _validate_audience(payload: dict[str, Any], audience: str) -> None:
    raw_audience = payload.get("aud")
    if isinstance(raw_audience, str):
        audiences = {raw_audience}
    elif isinstance(raw_audience, list) and all(isinstance(item, str) for item in raw_audience):
        audiences = set(raw_audience)
    else:
        raise TokenValidationError("aud 声明无效")
    if audience not in audiences:
        raise TokenValidationError("aud 声明不匹配")


def _is_timestamp(value: object) -> bool:
    """排除 bool、非数字与 NaN/Infinity；JWT 时间声明必须是有限数字。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _decode_json_object(segment: str) -> dict[str, Any]:
    raw = _b64url_decode(segment)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenValidationError("声明不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise TokenValidationError("声明必须是 JSON 对象")
    return value


def _b64url_decode(segment: str) -> bytes:
    if not segment or not segment.isascii():
        raise TokenValidationError("Base64URL 编码无效")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.b64decode(
            segment + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise TokenValidationError("Base64URL 编码无效") from exc
