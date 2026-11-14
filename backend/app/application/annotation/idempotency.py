"""标注 HTTP 写动作幂等适配层。

规则本体只有一处权威实现：:mod:`app.application.idempotency`
（`CLAUDE.md` 第五节「规则只允许存在一处权威实现」）。本文件只做两件事：

1. 把共享实现的 ``ValueError`` 适配成本域的 ``DomainValidationError``；
2. 把"同键不同体"适配成本域的 ``IdempotencyKeyReusedError``，
   以便沿用既有的域错误映射，对外 HTTP 形状完全不变。
"""

from __future__ import annotations

from app.application.idempotency import (
    DEFAULT_TTL,
    MAX_KEY_LENGTH,
    MIN_KEY_LENGTH,
    IdempotencyRecord,
    IdempotencyRepository,
    check_key_length,
    request_fingerprint,
)
from app.application.idempotency import IdempotencyGuard as _SharedGuard
from app.domain.annotation.errors import DomainValidationError, IdempotencyKeyReusedError

__all__ = [
    "DEFAULT_TTL",
    "MAX_KEY_LENGTH",
    "MIN_KEY_LENGTH",
    "IdempotencyGuard",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "request_fingerprint",
    "validate_key",
]


def validate_key(key: str | None) -> str | None:
    """长度校验；失败时抛本域 ``DomainValidationError`` 以走域错误映射。"""
    if key is None:
        return None
    try:
        check_key_length(key)
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc
    return key


class IdempotencyGuard(_SharedGuard):
    """共享守卫的域内别名：错误类型绑定到本域，调用方无需改动。"""

    def __init__(self, repository: IdempotencyRepository) -> None:
        super().__init__(
            repository,
            invalid_key_error=DomainValidationError,
            reused_error=IdempotencyKeyReusedError,
        )
