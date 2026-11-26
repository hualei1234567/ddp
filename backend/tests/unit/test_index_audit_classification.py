"""索引审计判定逻辑测试（审计 R6）。

索引审计的结论会驱动「删索引」这种不可逆操作。判定逻辑必须可单测——
尤其是「什么情况下**不能**下删除结论」。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.db.index_audit import SMALL_TABLE_ROWS, classify_index  # noqa: E402


def test_constraint_backed_index_never_droppable() -> None:
    """主键/唯一约束背后的索引不能删——即使零使用、即使表很大。

    删掉它会连带删掉约束语义，属于数据完整性事故而非性能优化。
    """
    assert (
        classify_index(is_constraint=True, sample_sufficient=True, table_rows=1_000_000)
        == "keep_constraint"
    )


def test_insufficient_sample_blocks_conclusion() -> None:
    """统计样本不足（新库/stat_reset）时不得给出删除结论。"""
    assert (
        classify_index(is_constraint=False, sample_sufficient=False, table_rows=1_000_000)
        == "review_after_growth"
    )


def test_small_table_index_is_not_a_drop_candidate() -> None:
    """小表上 PG 一定走 seq scan，索引零使用属正常；表长大后复查。"""
    assert (
        classify_index(is_constraint=False, sample_sufficient=True, table_rows=SMALL_TABLE_ROWS - 1)
        == "review_after_growth"
    )


def test_large_table_zero_scan_index_is_drop_candidate() -> None:
    """样本充分 + 大表 + 零使用 → 才是真正的可删候选（纯写成本）。"""
    assert (
        classify_index(is_constraint=False, sample_sufficient=True, table_rows=SMALL_TABLE_ROWS)
        == "drop_candidate"
    )


def test_constraint_takes_priority_over_small_sample() -> None:
    """优先级：约束背书高于样本充分性——样本不足也不能把约束索引标成可评估。"""
    assert (
        classify_index(is_constraint=True, sample_sufficient=False, table_rows=0)
        == "keep_constraint"
    )
