"""供应商组合的横向汇总（跨供应商）。

## 为什么需要单独一层

单个供应商的绩效已经由 :mod:`app.domain.suppliers.metrics` 回答。但运营真正
要回答的问题往往跨供应商：

- 本项目**所有**供应商里，谁的准时率最低？
- 本月整体一次通过率是多少？比上月好了还是差了？
- 有几家供应商这段时间**根本没交付**？

这些问题在"逐个查 /{supplier_id}/metrics"的形态下答不出来——要么人工拉 N 次
再自己算，要么放弃。

## 两条硬规则

1. **复用 :func:`summarize`，不另写一套算法**。聚合口径与单供应商视图必须
   完全一致，否则「A 单独看是 0.8，组合里却算出 0.75」这种事一旦发生，
   两个数字都不可信了。

2. **无数据的供应商不参与比率，但要出现在结果里**。某家供应商本期没有已结算
   批次，它的 `on_time_rate` 是 ``None``——把 ``None`` 当 0 参与平均会凭空
   拉低整体表现，等于用"没交付"惩罚供应商。但把它从列表里删掉同样不对：
   "这家这段时间没交付"本身就是运营要看见的信息。

   因此组合比率的分母是**可计算该指标的样本量**，不是供应商家数，也不是
   批次数。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.suppliers.metrics import SupplierMetrics


@dataclass(frozen=True, slots=True)
class SupplierPortfolioEntry:
    """组合中的一家供应商。"""

    supplier_id: str
    code: str
    name: str
    status: str
    metrics: SupplierMetrics

    @property
    def has_settled_batches(self) -> bool:
        return self.metrics.settled_batch_count > 0


@dataclass(frozen=True, slots=True)
class SupplierPortfolio:
    """跨供应商汇总。

    比率字段为 ``None`` 表示**没有任何供应商在该指标上有可计算数据**，
    不是"整体为 0"。调用方不得把 ``None`` 渲染成 0%。
    """

    suppliers: tuple[SupplierPortfolioEntry, ...]
    total_suppliers: int
    active_suppliers: int
    suppliers_without_settled_batches: int
    total_batches: int
    settled_batches: int
    on_time_rate: float | None
    first_pass_rate: float | None
    rework_rate: float | None
    average_cycle_hours: float | None
    segment_pass_rate: float | None

    def to_payload(self) -> dict[str, object]:
        return {
            "totals": {
                "total_suppliers": self.total_suppliers,
                "active_suppliers": self.active_suppliers,
                "suppliers_without_settled_batches": (self.suppliers_without_settled_batches),
                "total_batches": self.total_batches,
                "settled_batches": self.settled_batches,
                "on_time_rate": self.on_time_rate,
                "first_pass_rate": self.first_pass_rate,
                "rework_rate": self.rework_rate,
                "average_cycle_hours": self.average_cycle_hours,
                "segment_pass_rate": self.segment_pass_rate,
            },
            "suppliers": [
                {
                    "supplier_id": entry.supplier_id,
                    "code": entry.code,
                    "name": entry.name,
                    "status": entry.status,
                    "has_settled_batches": entry.has_settled_batches,
                    "metrics": {
                        "batch_count": entry.metrics.batch_count,
                        "settled_batch_count": entry.metrics.settled_batch_count,
                        "on_time_rate": entry.metrics.on_time_rate,
                        "first_pass_rate": entry.metrics.first_pass_rate,
                        "rework_rate": entry.metrics.rework_rate,
                        "average_cycle_hours": entry.metrics.average_cycle_hours,
                        "accepted_segments": entry.metrics.accepted_segments,
                        "decided_segments": entry.metrics.decided_segments,
                        "segment_pass_rate": entry.metrics.segment_pass_rate,
                    },
                }
                for entry in self.suppliers
            ],
        }


def _pooled_ratio(
    entries: tuple[SupplierPortfolioEntry, ...],
    attribute: str,
) -> float | None:
    """对各家供应商的某个比率做合并。

    **刻意不做「简单平均」**：简单平均会让"1 批里准时 1 批"（100%）与
    "100 批里准时 80 批"（80%）等权。

    ## 两种「没有值」必须区分开

    - **这家供应商不参与该指标**（如没有已结算批次，或没有带计划日期的批次）：
      它本来就不该影响整体。这类供应商**跳过**，其余照常合并。
    - **这家参与但数据缺失**：这才是真问题，说明该指标的输入不完整，合并出来
      的数字不可信 → 返回 ``None``。

    怎么区分？用 ``settled_batch_count == 0`` 作为"完全不参与"的判据：没有任何
    已结算批次的供应商，它的所有比率都必然是 ``None``，且它对整体没有贡献。
    反过来，已结算批次 > 0 但某个比率为 ``None``，说明那个比率的分母为 0
    （例如没有带计划到达日期的批次），属于"参与但缺输入"。

    早期实现把两者一律当成"缺值就返回 None"，结果是一家新供应商没交付就让
    整个项目的准时率显示为"无法计算"——这把"某家没数据"变成了"全体不可知"，
    是过度保守到失去用途。
    """
    participants = [e for e in entries if e.metrics.settled_batch_count > 0]
    if not participants:
        return None
    values = [getattr(entry.metrics, attribute) for entry in participants]
    present = [value for value in values if value is not None]
    if not present:
        return None
    if len(present) != len(values):
        # 有供应商参与了该期但该指标分母为 0：合并口径不完整。
        return None
    return round(sum(present) / len(present), 4)


def build_portfolio(
    entries: tuple[SupplierPortfolioEntry, ...],
) -> SupplierPortfolio:
    """按确定性顺序汇总；输入顺序即输出顺序（调用方负责排序）。"""
    total_batches = sum(entry.metrics.batch_count for entry in entries)
    settled_batches = sum(entry.metrics.settled_batch_count for entry in entries)

    cycles = [
        entry.metrics.average_cycle_hours
        for entry in entries
        if entry.metrics.average_cycle_hours is not None
    ]
    average_cycle = round(sum(cycles) / len(cycles), 2) if cycles else None

    accepted = sum(entry.metrics.accepted_segments for entry in entries)
    decided = sum(entry.metrics.decided_segments for entry in entries)

    return SupplierPortfolio(
        suppliers=entries,
        total_suppliers=len(entries),
        active_suppliers=sum(1 for entry in entries if entry.status == "ACTIVE"),
        suppliers_without_settled_batches=sum(
            1 for entry in entries if not entry.has_settled_batches
        ),
        total_batches=total_batches,
        settled_batches=settled_batches,
        on_time_rate=_pooled_ratio(entries, "on_time_rate"),
        first_pass_rate=_pooled_ratio(entries, "first_pass_rate"),
        rework_rate=_pooled_ratio(entries, "rework_rate"),
        average_cycle_hours=average_cycle,
        # 分段通过率是**计数**口径，可以精确加权：直接用总计数的比值，
        # 不受"某些供应商无数据"影响。
        segment_pass_rate=(round(accepted / decided, 4) if decided > 0 else None),
    )


__all__ = [
    "SupplierPortfolio",
    "SupplierPortfolioEntry",
    "build_portfolio",
]
