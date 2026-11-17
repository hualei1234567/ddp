"""供应商组合横向汇总的领域单测（纯函数，无 IO）。

三条被断言的语义，都是"少看一眼就会写成错的那种"：

1. 无数据的供应商**不参与比率计算**，但要**出现在结果里**——把 None 当 0
   参与平均会凭空拉低整体，等于用"没交付"惩罚供应商；把它从列表里删掉又会让
   "这家这段时间没交付"这个运营要看的信息消失；
2. 部分供应商缺某指标时，组合比率返回 ``None`` 而不是简单平均——简单平均
   会让"1 批全准时"与"100 批 80% 准时"等权；
3. 分段通过率是**计数**口径，直接按总计数加权，不受"某些供应商无数据"影响。
"""

from __future__ import annotations

from app.domain.suppliers.metrics import SupplierMetrics
from app.domain.suppliers.portfolio import SupplierPortfolioEntry, build_portfolio


def _metrics(
    *,
    batch_count: int = 10,
    settled: int = 10,
    on_time: float | None = 0.8,
    first_pass: float | None = 0.9,
    rework: float | None = 0.1,
    cycle: float | None = 12.0,
    accepted: int = 8,
    decided: int = 10,
) -> SupplierMetrics:
    return SupplierMetrics(
        batch_count=batch_count,
        settled_batch_count=settled,
        on_time_rate=on_time,
        first_pass_rate=first_pass,
        rework_rate=rework,
        average_cycle_hours=cycle,
        accepted_segments=accepted,
        decided_segments=decided,
        segment_pass_rate=(round(accepted / decided, 4) if decided > 0 else None),
    )


def _entry(
    supplier_id: str,
    *,
    status: str = "ACTIVE",
    **kwargs: object,
) -> SupplierPortfolioEntry:
    return SupplierPortfolioEntry(
        supplier_id=supplier_id,
        code=supplier_id,
        name=f"供应商 {supplier_id}",
        status=status,
        metrics=_metrics(**kwargs),  # type: ignore[arg-type]
    )


def test_pooled_ratio_averages_across_suppliers() -> None:
    portfolio = build_portfolio(
        (
            _entry("a", on_time=0.9, first_pass=0.8),
            _entry("b", on_time=0.7, first_pass=1.0),
        )
    )
    assert portfolio.on_time_rate == 0.8
    assert portfolio.first_pass_rate == 0.9


def test_supplier_without_data_is_listed_not_silently_dropped() -> None:
    """没有已结算批次的供应商必须出现在结果里，但比率不参与计算。"""
    portfolio = build_portfolio(
        (
            _entry("a", on_time=0.8),
            _entry(
                "b",
                batch_count=0,
                settled=0,
                on_time=None,
                first_pass=None,
                rework=None,
                cycle=None,
                accepted=0,
                decided=0,
            ),
        )
    )
    assert portfolio.total_suppliers == 2
    assert portfolio.suppliers_without_settled_batches == 1
    assert [entry.supplier_id for entry in portfolio.suppliers] == ["a", "b"]
    # 只有 a 有数据，比率就是 a 的值——不是 (0.8 + 0) / 2 = 0.4
    assert portfolio.on_time_rate == 0.8


def test_partial_missing_metric_yields_none_not_simple_average() -> None:
    """部分供应商缺某指标 → 组合比率 None，不用简单平均编造数字。"""
    portfolio = build_portfolio(
        (
            _entry("a", on_time=0.9),
            _entry("b", on_time=None),  # 有计划到达日期的批次为 0
        )
    )
    assert portfolio.on_time_rate is None


def test_all_missing_yields_none() -> None:
    portfolio = build_portfolio((_entry("a", on_time=None),))
    assert portfolio.on_time_rate is None


def test_segment_pass_rate_uses_actual_counts() -> None:
    """分段通过率按总计数加权：8/10 + 1/100 = 9/110，不是 (0.8+0.01)/2。"""
    portfolio = build_portfolio(
        (
            _entry("a", accepted=8, decided=10),
            _entry("b", accepted=1, decided=100),
        )
    )
    assert portfolio.segment_pass_rate == round(9 / 110, 4)


def test_segment_pass_rate_none_when_no_decided_segments() -> None:
    portfolio = build_portfolio((_entry("a", accepted=0, decided=0),))
    assert portfolio.segment_pass_rate is None


def test_empty_portfolio_is_honest() -> None:
    """一个供应商都没有时，所有比率为 None，计数为 0 —— 不是伪造一行 0。"""
    portfolio = build_portfolio(())
    assert portfolio.total_suppliers == 0
    assert portfolio.on_time_rate is None
    assert portfolio.segment_pass_rate is None
    payload = portfolio.to_payload()
    assert payload["suppliers"] == []
    assert payload["totals"]["total_suppliers"] == 0  # type: ignore[index]


def test_active_supplier_count() -> None:
    portfolio = build_portfolio((_entry("a", status="ACTIVE"), _entry("b", status="DRAFT")))
    assert portfolio.active_suppliers == 1


def test_payload_is_json_safe() -> None:
    portfolio = build_portfolio((_entry("a"),))
    payload = portfolio.to_payload()
    assert isinstance(payload["totals"], dict)
    assert isinstance(payload["suppliers"], list)
    assert payload["suppliers"][0]["supplier_id"] == "a"  # type: ignore[index]
