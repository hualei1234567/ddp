"""乐观锁前置条件解析。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from app.core.errors import PreconditionRequiredError, ValidationFailedError


def require_expected_version(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int:
    """要求 ``If-Match: "<version>"``；缺失 428，非法 422。"""
    if if_match is None or not if_match.strip():
        raise PreconditionRequiredError()
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
