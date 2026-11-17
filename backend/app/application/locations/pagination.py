"""场地域列表分页：不透明游标 + 键集分页。

与 ``app.application.collection.pagination`` / ``devices.pagination`` 同构，
差异只有一处：错误类型绑定到本域 ``DomainValidationError``。不合并成一份
公共分页模块——那会要求三个域共享同一个错误类，从而把"哪个域的错误"
这个由 ``app/main.py`` 按类型分派的语义抹平。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID

from app.domain.locations.errors import DomainValidationError

T = TypeVar("T")

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_FETCH_LIMIT = MAX_LIMIT + 1


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


def normalize_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise DomainValidationError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
    return limit


def encode_cursor(value: UUID) -> str:
    return base64.urlsafe_b64encode(value.bytes).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> UUID | None:
    if cursor is None or cursor == "":
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(f"{cursor}{padding}".encode("ascii"))
        if len(raw) != 16:
            raise ValueError("游标长度非法")
        return UUID(bytes=raw)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise DomainValidationError("cursor 非法") from exc


def build_page(
    items: list[T],
    *,
    limit: int,
    id_of: Callable[[T], UUID],
) -> Page[T]:
    """仓储多取一条用于判断是否还有下一页。"""
    if len(items) <= limit:
        return Page(items=items, next_cursor=None)
    visible = items[:limit]
    return Page(items=visible, next_cursor=encode_cursor(id_of(visible[-1])))


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_FETCH_LIMIT",
    "MAX_LIMIT",
    "Page",
    "build_page",
    "decode_cursor",
    "encode_cursor",
    "normalize_limit",
]
