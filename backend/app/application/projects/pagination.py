"""项目 / 成员列表分页：不透明游标 + 键集分页。

与 ``application.locations.pagination`` 同构，差异只有两处：

1. 游标承载的是**字符串业务键**（项目 ``code``、成员 ``user_id`` 的字符串形式），
   而不是 UUID 字节——项目列表按 ``code`` 升序，用 UUID 做游标会退化成随机顺序；
2. 错误类型直接用 ``core.errors.ValidationFailedError``：项目域没有自己的领域
   错误族（见 ``application/projects/use_cases`` 文档），无需再做一层换算。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.core.errors import ValidationFailedError

T = TypeVar("T")

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_FETCH_LIMIT = MAX_LIMIT + 1
# 项目 code 上限 64，成员游标是 36 位 UUID 字符串；取两者较大值再加余量。
_MAX_CURSOR_LENGTH = 128


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


def normalize_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationFailedError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
    return limit


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> str | None:
    if cursor is None or cursor == "":
        return None
    if len(cursor) > _MAX_CURSOR_LENGTH:
        raise ValidationFailedError("cursor 非法")
    try:
        padding = "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(f"{cursor}{padding}".encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ValidationFailedError("cursor 非法") from exc


def build_page(
    items: list[T],
    *,
    limit: int,
    key_of: Callable[[T], str],
) -> Page[T]:
    """仓储多取一条用于判断是否还有下一页。"""
    if len(items) <= limit:
        return Page(items=items, next_cursor=None)
    visible = items[:limit]
    return Page(items=visible, next_cursor=encode_cursor(key_of(visible[-1])))


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
