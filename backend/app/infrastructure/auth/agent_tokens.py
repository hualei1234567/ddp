"""Agent 凭据的生成与哈希（与用户 JWT 完全分离）。

- 令牌是 ``secrets.token_urlsafe(32)`` 生成的 256 位随机串，带类型前缀：
  ``ddp_pair_`` 为一次性配对凭证，``ddp_agt_`` 为 Agent 专属 token。
  前缀只用于**快速拒绝错误类型**与方便密钥扫描工具识别，不携带任何声明。
- 落库只存 SHA-256 十六进制摘要。输入是 256 位均匀随机数，不存在字典/彩虹表
  攻击面，因此无需口令哈希（bcrypt/argon2）的慢哈希；用户口令类秘密不得套用本模块。
- 本模块不做任何日志输出，也不提供“从哈希还原”的路径。

JWT 校验见 :mod:`app.infrastructure.auth.jwt`；两类凭据没有任何共享的签名密钥，
用户 JWT 无法通过 Agent 鉴权，Agent token 也无法通过用户鉴权。
"""

from __future__ import annotations

import hashlib
import secrets

AGENT_TOKEN_PREFIX = "ddp_agt_"  # noqa: S105 - 类型前缀，非凭据
PAIRING_TOKEN_PREFIX = "ddp_pair_"  # noqa: S105 - 类型前缀，非凭据
_TOKEN_BYTES = 32
_MAX_TOKEN_LENGTH = 128


def generate_agent_token() -> str:
    return f"{AGENT_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def generate_pairing_token() -> str:
    return f"{PAIRING_TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_token(token: str) -> str:
    """SHA-256 十六进制摘要（64 字符）；调用方必须先用 :func:`has_prefix` 校验类型。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def has_prefix(token: str, prefix: str) -> bool:
    """类型前缀与长度的快速校验；不合格的输入不进入数据库查询。"""
    return (
        isinstance(token, str)
        and token.startswith(prefix)
        and len(token) <= _MAX_TOKEN_LENGTH
        and token.isascii()
    )
