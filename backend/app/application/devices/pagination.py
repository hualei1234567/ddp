"""设备域列表分页。

对外游标是不透明字符串，内部只记录最后一条记录的 UUID。
分页使用 ``id > after_id`` 的键集查询，避免深度 offset 与并发插入导致的错页。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID

from app.domain.devices.errors import DomainValidationError

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
