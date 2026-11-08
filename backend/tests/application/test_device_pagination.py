"""设备域分页游标单元测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.application.devices.pagination import (
    build_page,
    decode_cursor,
    encode_cursor,
)
from app.domain.devices.errors import DomainValidationError


def test_cursor_roundtrip() -> None:
    value = uuid4()

    decoded = decode_cursor(encode_cursor(value))

    assert decoded == value


def test_empty_cursor_is_none() -> None:
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


def test_invalid_cursor_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        decode_cursor("not-a-cursor")


def test_page_uses_last_visible_item_for_next_cursor() -> None:
    items = [uuid4() for _ in range(3)]

    page = build_page(items, limit=2, id_of=lambda item: item)

    assert page.items == items[:2]
    assert decode_cursor(page.next_cursor) == items[1]
