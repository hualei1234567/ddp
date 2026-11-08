"""标注域 HTTP 分页辅助。

游标对客户端不透明，只承载最后一条记录的 ID；非法游标返回 422。
应用层仓储负责键集排序，本模块只做编解码与截断。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import UUID

from app.core.errors import ValidationFailedError

T = TypeVar("T")

MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


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
        raise ValidationFailedError("cursor 非法") from exc


def build_page(
    items: list[T],
    *,
    limit: int,
    id_of: Callable[[T], UUID],
) -> Page[T]:
    """按 ``limit`` 截断；多取的一条用于判断是否还有下一页。"""
    if len(items) <= limit:
        return Page(items=items, next_cursor=None)
    visible = items[:limit]
    return Page(items=visible, next_cursor=encode_cursor(id_of(visible[-1])))


def fetch_limit(limit: int) -> int:
    """多取一条；不超过仓储允许的最大 limit。"""
    return min(limit + 1, MAX_LIMIT + 1)
