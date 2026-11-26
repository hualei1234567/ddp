"""HS256 JWT 校验适配器。

签名与 JSON 解析使用 PyJWT（CLAUDE.md《复用优先与代码质量标准》第二节：
不得手写已知有标准解法的底层协议，HS256 校验在列举范围内）。本模块只把
PyJWT 的结果映射为领域声明，并施加 DDP 自己的严格策略：

- 只接受 ``HS256``，拒绝 ``none``、非对称算法与未处理的 ``crit`` 扩展；
- 严格校验签名、``exp``、``nbf``、``iss``、``aud``；
- ``exp``/``nbf`` 必须是有限数值，显式排除 ``bool``、``NaN``、``Infinity``；
- 所有失败都抛出 :class:`TokenValidationError`，由 API 层统一映射为 401，
  不向客户端区分“签名错误”“已过期”等内部原因。

本模块只做协议解析与校验，不依赖 FastAPI、SQLAlchemy 或业务领域模型。
"""

from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass
from typing import Any

import jwt

_ALGORITHM = "HS256"
_ALLOWED_ALGORITHMS = ("HS256",)
_MAX_TOKEN_LENGTH = 8192
_MAX_SUBJECT_LENGTH = 512

# 自行校验声明：PyJWT 只负责签名与结构，claim 策略由本模块统一裁决，
# 避免“库的宽松语义”与 DDP 的严格语义之间出现两套判定。
_DECODE_OPTIONS = {
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
    "verify_iss": False,
    "verify_aud": False,
    "verify_sub": False,
    "require": [],
}


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

    header = _decode_header(token)
    if header.get("alg") != _ALGORITHM:
        raise TokenValidationError("不支持的签名算法")
    if "crit" in header:
        raise TokenValidationError("不支持的 crit 扩展")

    payload = _decode_payload(token, secret)

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


def _decode_header(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        # PyJWT 2.15+ 在 ``get_unverified_header`` 阶段即校验并拒绝 ``crit``
        # 扩展，异常先于本模块的显式检查抛出，会把既有的「不支持的 crit 扩展」
        # 语义覆盖成泛化的「令牌格式错误」。识别该场景，收敛回本模块的拒绝语义。
        if "crit" in str(exc).lower():
            raise TokenValidationError("不支持的 crit 扩展") from exc
        raise TokenValidationError("令牌格式错误") from exc
    if not isinstance(header, dict):
        raise TokenValidationError("令牌头必须是 JSON 对象")
    return header


def _decode_payload(token: str, secret: str) -> dict[str, Any]:
    try:
        # PyJWT 2.15+ 对短 HS256 密钥发 InsecureKeyLengthWarning。密钥强度由
        # ``Settings.validate_runtime_security`` 在应用启动时统一强制（本地环境
        # 显式允许短开发密钥），解码路径不重复裁决，避免该提示性告警在
        # filterwarnings=error 的测试环境下升级为异常。
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=jwt.warnings.InsecureKeyLengthWarning)
            payload = jwt.decode(
                token,
                secret,
                algorithms=list(_ALLOWED_ALGORITHMS),
                options=_DECODE_OPTIONS,
            )
    except jwt.InvalidTokenError as exc:
        # 签名错误、格式错误、段数不符等统一收敛，不外泄内部原因。
        raise TokenValidationError("令牌校验失败") from exc
    if not isinstance(payload, dict):
        raise TokenValidationError("声明必须是 JSON 对象")
    return payload


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
