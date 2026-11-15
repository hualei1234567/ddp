"""导入抽样语义测试。

背景（真实缺陷）：``sample_limit`` 此前只写入任务行与事件负载，
``ImportRunner`` 执行时**从未读取**，导致 ``scope=SAMPLE, sample_limit=10``
的导入实际仍拉全量 preview（Human-1M 恒为 15 条）。规模因此不可控。

本测试锁定修复后的契约：
- ``SAMPLE`` 取解析后 Episode 序列的**前 N 个**（元数据顺序，确定性）；
- 抽样单位是 Episode，调用方随后遍历其全部机位，因此不会出现半条机位；
- ``SPLIT`` / ``FULL`` 不截断；
- 参数非法时显式失败，不静默退化成全量。
"""

from __future__ import annotations

import pytest
from app.application.datasources.import_runner import select_episodes
from app.domain.datasources.errors import DomainValidationError


def _episodes(count: int) -> tuple[object, ...]:
    """构造可辨识顺序的 Episode 占位对象（真实实现只读 episode_key 之外的属性）。"""

    class _Episode:
        def __init__(self, index: int) -> None:
            self.episode_key = f"demo_{index:03d}"

    return tuple(_Episode(i) for i in range(1, count + 1))


def test_sample_takes_first_n_in_metadata_order() -> None:
    """确定性：取前 N 个，不做随机抽样，保证可复现。"""
    picked = select_episodes(_episodes(15), scope="SAMPLE", sample_limit=10)

    assert [e.episode_key for e in picked] == [f"demo_{i:03d}" for i in range(1, 11)]


def test_sample_limit_larger_than_available_keeps_all() -> None:
    """数据量本身少于上限不是错误；不能因此失败或补造数据。"""
    picked = select_episodes(_episodes(15), scope="SAMPLE", sample_limit=100)

    assert len(picked) == 15


def test_sample_limit_equal_to_available_is_identity() -> None:
    picked = select_episodes(_episodes(10), scope="SAMPLE", sample_limit=10)

    assert len(picked) == 10


def test_split_scope_is_not_truncated() -> None:
    """SPLIT 语义是整段导入，不能被 sample_limit 误伤。"""
    picked = select_episodes(_episodes(15), scope="SPLIT", sample_limit=3)

    assert len(picked) == 15


def test_full_scope_is_not_truncated() -> None:
    picked = select_episodes(_episodes(15), scope="FULL", sample_limit=1)

    assert len(picked) == 15


def test_sample_without_limit_fails_loudly() -> None:
    """域模型已禁止该组合；执行层兜底失败，绝不静默全量。"""
    with pytest.raises(DomainValidationError, match="sample_limit"):
        select_episodes(_episodes(5), scope="SAMPLE", sample_limit=None)


def test_sample_with_non_positive_limit_fails_loudly() -> None:
    with pytest.raises(DomainValidationError, match="sample_limit"):
        select_episodes(_episodes(5), scope="SAMPLE", sample_limit=0)


def test_selection_preserves_original_order_and_identity() -> None:
    """抽样必须保持原始顺序，否则"前 N 条"不可复现。"""
    source = _episodes(15)
    picked = select_episodes(source, scope="SAMPLE", sample_limit=10)

    assert picked == source[:10]
