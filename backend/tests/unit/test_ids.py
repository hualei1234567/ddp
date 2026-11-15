"""G-18：设备域 ID 使用 UUIDv7。

断言的是 v7 的**性质**（版本位、时间有序、内嵌时间戳可解析），
而不是某一次生成的具体值——否则测试会变成随机数比对。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.common.ids import is_uuid_v7, new_id, timestamp_ms


def test_new_id_is_uuid_v7() -> None:
    value = new_id()
    assert isinstance(value, UUID)
    assert value.version == 7
    assert is_uuid_v7(value)


def test_new_ids_are_time_ordered() -> None:
    """同进程内连续生成的 v7 必须严格递增——这是分页稳定性的前提。"""
    ids = [new_id() for _ in range(500)]
    assert all(left < right for left, right in zip(ids, ids[1:], strict=False))
    assert len(set(ids)) == 500


def test_timestamp_embedded_in_id_is_current_time() -> None:
    before = int(datetime.now(UTC).timestamp() * 1000)
    value = new_id()
    after = int(datetime.now(UTC).timestamp() * 1000)
    assert before <= timestamp_ms(value) <= after


def test_uuid4_is_not_mistaken_for_v7() -> None:
    legacy = uuid4()
    assert not is_uuid_v7(legacy)
    with pytest.raises(ValueError, match="UUIDv7"):
        timestamp_ms(legacy)


def test_v7_ids_sort_into_creation_order_across_milliseconds() -> None:
    import time

    first = new_id()
    time.sleep(0.003)
    second = new_id()
    assert first < second
