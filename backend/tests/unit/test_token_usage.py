"""Token 成本台账领域单测（纯函数，无 IO）。

被断言的语义都是"少看一眼就会写错的那种"：

1. **未配价是 ``None`` 不是 0**：``None`` 是"没有价格数据"，0 是"免费"，
   混用会把漏配价格当成省钱（与"没测到当 0"是同一类错误）；
2. **聚合分桶**：按 ``(model_id, purpose)`` 聚合、按键排序（确定性）；
   ``cost_estimated_tokens`` 只含有价可算的 token——无价模型的 tokens
   不能混进"已计价成本对应的量"，否则"每 token 均价"会被稀释；
3. **ROI 只做算式**：工时节省量由调用方提供，本层不推导；
   ``token_cost`` 为 0 或无价时 ROI 是 ``None``——除零不是无穷大，
   "没有成本数据"也不是"成本为零"；
4. **不变量**：tokens/cost 非负、purpose 合法、时间必须带时区、
   模型身份必填非空、跨项目聚合显式失败。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.token_usage import (
    TokenUsagePurpose,
    TokenUsageRecord,
    aggregate_usage,
    compute_cost,
    estimate_cost,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
_PRICE = {"m1": {"input": 0.002, "output": 0.008}}


def _record(
    *,
    project_id=None,
    model_id: str = "m1",
    purpose: object = TokenUsagePurpose.PRE_LABEL,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost: object = Decimal("0.0060"),
) -> TokenUsageRecord:
    return TokenUsageRecord(
        usage_id=uuid4(),
        project_id=project_id or uuid4(),
        model_id=model_id,
        model_version="1.2.0",
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        occurred_at=NOW,
        cost_estimate=cost,
    )


# ------------------------------------------------------------------ 估算
def test_estimate_cost_with_price() -> None:
    """1000 in @0.002 + 500 out @0.008 = 0.002 + 0.004 = 0.006。"""
    assert estimate_cost(1000, 500, _PRICE, model_id="m1") == Decimal("0.0060")


def test_estimate_cost_unknown_model_returns_none() -> None:
    """模型不在价格表：不猜价，返回 None 而不是 0。"""
    assert estimate_cost(1000, 500, _PRICE, model_id="unknown") is None


def test_estimate_cost_no_price_map_returns_none() -> None:
    assert estimate_cost(1000, 500, None, model_id="m1") is None


def test_estimate_cost_missing_component_returns_none() -> None:
    """缺 input 或 output 单价都不猜。"""
    assert estimate_cost(1000, 500, {"m1": {"input": 0.002}}, model_id="m1") is None
    assert estimate_cost(1000, 500, {"m1": {}}, model_id="m1") is None


def test_zero_price_is_zero_not_none() -> None:
    """价格为 0 是真免费：结果 0，不是 None。"""
    assert compute_cost(
        model_id="m1",
        input_tokens=1000,
        output_tokens=0,
        price_map={"m1": {"input": 0, "output": 0}},
    ) == Decimal("0.0000")


def test_price_unit_documented() -> None:
    """价格单位必须随常量声明，防止调用方按错误单位换算。"""
    from app.domain.annotation.token_usage import PRICE_UNIT

    assert "1k" in PRICE_UNIT


# ------------------------------------------------------------------ 不变量
def test_negative_tokens_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _record(input_tokens=-1)
    with pytest.raises(DomainValidationError):
        _record(output_tokens=-1)


def test_zero_tokens_allowed() -> None:
    """0 合法：缓存命中可无输入、纯缓存响应可无输出。"""
    record = _record(input_tokens=0, output_tokens=0, cost=Decimal("0"))
    assert record.total_tokens == 0


def test_negative_cost_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _record(cost=Decimal("-0.01"))


def test_invalid_purpose_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _record(purpose="FINE_TUNE")


def test_naive_datetime_rejected() -> None:
    """时间必须带时区（与全库时间语义一致）。"""
    with pytest.raises(DomainValidationError):
        TokenUsageRecord(
            usage_id=uuid4(),
            project_id=uuid4(),
            model_id="m1",
            model_version="1",
            purpose=TokenUsagePurpose.EMBED,
            input_tokens=0,
            output_tokens=0,
            occurred_at=datetime(2026, 1, 1),
        )


def test_empty_model_identity_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _record(model_id="")
    with pytest.raises(DomainValidationError):
        record = _record()
        object.__setattr__(record, "model_version", " ")
        TokenUsageRecord(
            usage_id=record.usage_id,
            project_id=record.project_id,
            model_id="m1",
            model_version="  ",
            purpose=record.purpose,
            input_tokens=0,
            output_tokens=0,
            occurred_at=NOW,
        )


def test_purpose_accepts_string_and_enum() -> None:
    assert _record(purpose="EMBED").purpose is TokenUsagePurpose.EMBED
    assert _record(purpose=TokenUsagePurpose.LLM_JUDGE).purpose is TokenUsagePurpose.LLM_JUDGE


def test_payload_cost_is_string() -> None:
    """金额对外用字符串（MEM-INC-008 同族：JSON 双精度丢精度）。"""
    payload = _record().to_payload()
    assert payload["cost_estimate"] == "0.0060"
    assert isinstance(payload["cost_estimate"], str)


def test_payload_cost_none_when_unpriced() -> None:
    assert _record(cost=None).to_payload()["cost_estimate"] is None


# ------------------------------------------------------------------ 聚合
def test_aggregate_groups_by_model_and_purpose() -> None:
    pid = uuid4()
    records = [
        _record(project_id=pid, model_id="m1", purpose=TokenUsagePurpose.PRE_LABEL),
        _record(project_id=pid, model_id="m1", purpose=TokenUsagePurpose.PRE_LABEL),
        _record(project_id=pid, model_id="m1", purpose=TokenUsagePurpose.EMBED, cost=None),
        _record(project_id=pid, model_id="m2", purpose=TokenUsagePurpose.PRE_LABEL),
    ]
    ledger = aggregate_usage(records)
    keys = [(entry.model_id, entry.purpose) for entry in ledger.entries]
    assert keys == [
        ("m1", TokenUsagePurpose.EMBED),
        ("m1", TokenUsagePurpose.PRE_LABEL),
        ("m2", TokenUsagePurpose.PRE_LABEL),
    ]
    pre_label = ledger.entries[1]
    assert pre_label.call_count == 2
    assert pre_label.input_tokens == 2000
    assert pre_label.output_tokens == 1000
    assert pre_label.cost_estimate == Decimal("0.0120")


def test_aggregate_priced_tokens_exclude_unpriced() -> None:
    """无价桶 cost_estimated_tokens=0；不把无价 token 混进已计价量。"""
    pid = uuid4()
    ledger = aggregate_usage(
        [
            _record(project_id=pid, model_id="m1", cost=None, input_tokens=9999),
            _record(project_id=pid, model_id="m1", cost=Decimal("0.0060")),
        ]
    )
    entry = ledger.entries[0]
    assert entry.call_count == 2
    assert entry.cost_estimate == Decimal("0.0060")
    assert entry.cost_estimated_tokens == 1500  # 只有有价那一条
    assert entry.total_tokens == 11999  # 全量 tokens 照常统计


def test_aggregate_total_none_when_all_unpriced() -> None:
    """全部未配价：合计 None（不是 0）。"""
    pid = uuid4()
    ledger = aggregate_usage([_record(project_id=pid, cost=None)])
    assert ledger.total_cost_estimate is None


def test_aggregate_mixed_pricing_sums_priced_only() -> None:
    pid = uuid4()
    ledger = aggregate_usage(
        [
            _record(project_id=pid, cost=Decimal("0.0060")),
            _record(project_id=pid, cost=None),
        ]
    )
    assert ledger.total_cost_estimate == Decimal("0.0060")


def test_aggregate_rejects_cross_project() -> None:
    """成本台账不跨项目混合：混入其他项目的记录显式失败。"""
    with pytest.raises(DomainValidationError):
        aggregate_usage([_record(), _record()])


def test_aggregate_rejects_empty_input() -> None:
    """空输入显式失败：静默返回空台账会把"忘传数据"掩盖成"零成本"。"""
    with pytest.raises(DomainValidationError):
        aggregate_usage([])


def test_aggregate_rejects_non_record_item() -> None:
    with pytest.raises(DomainValidationError):
        aggregate_usage([{"model_id": "m1"}])


# ------------------------------------------------------------------ ROI
def test_roi_compare_arithmetic() -> None:
    """只做算式：saved = 2.5h * 30 = 75；roi = 75 / 0.006 = 12500。"""
    pid = uuid4()
    ledger = aggregate_usage([_record(project_id=pid)])
    roi = ledger.roi_compare(human_review_hours_saved=2.5, hourly_rate=30.0)
    assert roi.saved_labor_cost == Decimal("75.0000")
    assert roi.token_cost == Decimal("0.0060")
    assert roi.roi == Decimal("12500.0000")


def test_roi_none_when_no_priced_usage() -> None:
    """token_cost 无价时 ROI 是 None——不是无穷大也不是 0。"""
    pid = uuid4()
    ledger = aggregate_usage([_record(project_id=pid, cost=None)])
    roi = ledger.roi_compare(human_review_hours_saved=2.5, hourly_rate=30.0)
    assert roi.token_cost is None
    assert roi.roi is None
    assert roi.saved_labor_cost == Decimal("75.0000")


def test_roi_none_when_zero_cost() -> None:
    """token_cost 为 0（真免费）时除零返回 None。"""
    pid = uuid4()
    ledger = aggregate_usage(
        [_record(project_id=pid, cost=Decimal("0"), input_tokens=0, output_tokens=0)]
    )
    roi = ledger.roi_compare(human_review_hours_saved=2.5, hourly_rate=30.0)
    assert roi.token_cost == Decimal("0.0000")
    assert roi.roi is None


def test_roi_rejects_negative_inputs() -> None:
    pid = uuid4()
    ledger = aggregate_usage([_record(project_id=pid)])
    with pytest.raises(DomainValidationError):
        ledger.roi_compare(human_review_hours_saved=-1, hourly_rate=30)
    with pytest.raises(DomainValidationError):
        ledger.roi_compare(human_review_hours_saved=1, hourly_rate=-30)


def test_roi_payload_uses_strings() -> None:
    pid = uuid4()
    ledger = aggregate_usage([_record(project_id=pid)])
    payload = ledger.roi_compare(human_review_hours_saved=2.5, hourly_rate=30.0).to_payload()
    assert payload["token_cost"] == "0.0060"
    assert payload["saved_labor_cost"] == "75.0000"
    assert payload["roi"] == "12500.0000"
