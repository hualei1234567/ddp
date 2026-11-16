"""乐观锁前置条件解析。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from app.core.errors import PreconditionRequiredError, ValidationFailedError


def _parse_if_match(if_match: str | None) -> int | None:
    """解析 ``If-Match``；缺失返回 ``None``，格式非法抛 422。"""
    if if_match is None or not if_match.strip():
        return None
    raw = if_match.strip()
    if raw.startswith("W/"):
        raw = raw[2:].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    try:
        version = int(raw)
    except ValueError as exc:
        raise ValidationFailedError("If-Match 必须是正整数版本号") from exc
    if version < 1:
        raise ValidationFailedError("If-Match 必须是正整数版本号")
    return version


def require_expected_version(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int:
    """要求 ``If-Match: "<version>"``；缺失 428，非法 422。"""
    version = _parse_if_match(if_match)
    if version is None:
        raise PreconditionRequiredError()
    return version


def optional_expected_version(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int | None:
    """可选 ``If-Match``：存在则按同一语义解析，缺失返回 ``None``。

    用于"创建或修改"同形的 PUT（如项目配额）：资源不存在时允许不带
    ``If-Match`` 创建；已存在时由用例要求必须携带，避免静默覆盖。
    """
    return _parse_if_match(if_match)


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """写操作必须携带幂等键；缺失 428，长度非法留给参数校验返回 422。"""
    if idempotency_key is None or not idempotency_key.strip():
        raise PreconditionRequiredError("缺少 Idempotency-Key 头")
    value = idempotency_key.strip()
    if len(value) < 8 or len(value) > 128:
        raise ValidationFailedError("Idempotency-Key 长度必须在 8 到 128 之间")
    return value
