"""ID 生成：UUIDv7（全仓唯一实现）。

为什么用 UUIDv7 而不是 UUIDv4：v4 完全随机，主键在 B-tree 里随机分布，
时间相邻的写入落在索引的随机页上，导致写放大与缓存命中率下降；
按时间分页也只能靠 ``created_at``，一旦同一毫秒多条记录就无法稳定排序。

实现用 ``uuid-utils``（Rust 实现，CPython ``uuid`` 的 drop-in 替代）。
**不用 ``uuid6`` 库**：它为了"保证唯一"每次生成都把时间戳前推 1ms，
生成 200 个 ID 就让内嵌时间跑到未来 200ms，ID 里的时间不再等于创建时间，
按时间排障会得到错的时间线。

v7 前 48 位是毫秒时间戳，同毫秒内递增，因此：

- 主键索引近似顺序写入；
- ``ORDER BY id`` 等价于按创建时间排序，分页游标天然稳定；
- ID 自带时间信息，排障时不用再关联 ``created_at``。

**不改变已有 ID 的含义**：v7 与 v4 都是 128 位、都通过 ``uuid.UUID`` 校验，
数据库列类型与约束不用改，旧数据无需迁移。这里只是把"新生成的 ID"换成 v7。
"""

from __future__ import annotations

from uuid import UUID

from uuid_utils import uuid7 as _uuid7


def new_id() -> UUID:
    """生成 UUIDv7（时间有序）。"""
    return UUID(str(_uuid7()))


def is_uuid_v7(value: UUID) -> bool:
    return value.version == 7


def timestamp_ms(value: UUID) -> int:
    """取 v7 ID 内嵌的毫秒时间戳；非 v7 抛 ``ValueError``。"""
    if not is_uuid_v7(value):
        raise ValueError("只有 UUIDv7 内嵌时间戳")
    return value.int >> 80


__all__ = ["is_uuid_v7", "new_id", "timestamp_ms"]
