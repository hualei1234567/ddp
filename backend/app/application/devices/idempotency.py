"""设备域 HTTP 写动作幂等适配层。

规则本体在 :mod:`app.application.idempotency`（唯一权威实现）；
本文件只把错误类型绑定到设备域，沿用既有域错误映射。
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
from app.domain.devices.errors import DomainValidationError, IdempotencyKeyReusedError

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
    if key is None:
        return None
    try:
        check_key_length(key)
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc
    return key


class IdempotencyGuard(_SharedGuard):
    def __init__(self, repository: IdempotencyRepository) -> None:
        super().__init__(
            repository,
            invalid_key_error=DomainValidationError,
            reused_error=IdempotencyKeyReusedError,
        )
