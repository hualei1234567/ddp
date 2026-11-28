"""Token 预算与告警评估测试（V61-019）。

重点钉住三条语义：

1. **无数据 ≠ 0**：``usage=None`` 判 ``UNKNOWN``，不触发告警、也不假装在预算内；
2. **未设预算 ≠ 预算内**：``limit=None`` 判 ``UNLIMITED``，与 ``OK`` 分开；
3. **任一维度超限即整体超限**，且 ``breached_dimensions`` 可作告警去重键。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.token_budget import (
    BudgetDimension,
    BudgetUsage,
    BudgetVerdict,
    TokenBudgetPolicy,
    TokenBudgetStatus,
    evaluate_token_budget,
)


def test_no_policy_at_all_is_unlimited_not_ok() -> None:
    """一个维度都没设 → 整体 UNLIMITED（不是 OK：没预算不等于在预算内）。"""
    evaluation = evaluate_token_budget(BudgetUsage(total_tokens=10**9), TokenBudgetPolicy())

    assert evaluation.status is TokenBudgetStatus.UNLIMITED
    assert all(item.verdict is BudgetVerdict.UNLIMITED for item in evaluation.dimensions)
    assert evaluation.policy.is_configured is False


def test_under_warning_ratio_is_ok() -> None:
    evaluation = evaluate_token_budget(
        BudgetUsage(monthly_tokens=500),
        TokenBudgetPolicy(monthly_tokens=1000, warning_ratio=0.8),
    )

    assert evaluation.status is TokenBudgetStatus.OK
    monthly = next(
        item for item in evaluation.dimensions if item.dimension is BudgetDimension.MONTHLY_TOKENS
    )
    assert monthly.verdict is BudgetVerdict.OK
    assert monthly.ratio == "0.5000"


def test_at_warning_ratio_is_warning() -> None:
    evaluation = evaluate_token_budget(
        BudgetUsage(monthly_tokens=800),
        TokenBudgetPolicy(monthly_tokens=1000, warning_ratio=0.8),
    )

    assert evaluation.status is TokenBudgetStatus.WARNING
    assert evaluation.warning_dimensions == (BudgetDimension.MONTHLY_TOKENS,)
    assert evaluation.breached_dimensions == ()


def test_exact_limit_is_warning_not_exceeded() -> None:
    """恰好等于上限仍算 WARNING（只有严格超过才是 EXCEEDED）。"""
    evaluation = evaluate_token_budget(
        BudgetUsage(total_tokens=1000),
        TokenBudgetPolicy(total_tokens=1000),
    )

    assert evaluation.status is TokenBudgetStatus.WARNING


def test_over_limit_is_exceeded_and_reports_dimension() -> None:
    evaluation = evaluate_token_budget(
        BudgetUsage(total_tokens=1001),
        TokenBudgetPolicy(total_tokens=1000),
    )

    assert evaluation.status is TokenBudgetStatus.EXCEEDED
    assert evaluation.breached_dimensions == (BudgetDimension.TOTAL_TOKENS,)


def test_unknown_usage_does_not_trigger_alert() -> None:
    """成本无数据（None）→ UNKNOWN，不告警、不算 0。"""
    evaluation = evaluate_token_budget(
        BudgetUsage(total_tokens=10, total_cost_usd=None),
        TokenBudgetPolicy(total_tokens=1000, total_cost_usd=Decimal("100")),
    )

    cost = next(
        item for item in evaluation.dimensions if item.dimension is BudgetDimension.TOTAL_COST
    )
    assert cost.verdict is BudgetVerdict.UNKNOWN
    assert cost.usage is None
    assert cost.ratio is None
    assert evaluation.status is TokenBudgetStatus.OK


def test_one_exceeded_dimension_wins_over_warnings() -> None:
    evaluation = evaluate_token_budget(
        BudgetUsage(monthly_tokens=900, monthly_cost_usd=Decimal("12")),
        TokenBudgetPolicy(
            monthly_tokens=1000,
            monthly_cost_usd=Decimal("10"),
            warning_ratio=0.8,
        ),
    )

    assert evaluation.status is TokenBudgetStatus.EXCEEDED
    assert evaluation.breached_dimensions == (BudgetDimension.MONTHLY_COST,)
    assert evaluation.warning_dimensions == (BudgetDimension.MONTHLY_TOKENS,)


def test_cost_ratio_uses_four_decimals() -> None:
    evaluation = evaluate_token_budget(
        BudgetUsage(total_cost_usd=Decimal("3.3333")),
        TokenBudgetPolicy(total_cost_usd=Decimal("10")),
    )

    cost = next(
        item for item in evaluation.dimensions if item.dimension is BudgetDimension.TOTAL_COST
    )
    assert cost.ratio == "0.3333"


def test_payload_is_json_safe() -> None:
    """对外输出必须可 JSON 序列化：金额是字符串，不含 Decimal 对象。"""
    import json

    evaluation = evaluate_token_budget(
        BudgetUsage(total_cost_usd=Decimal("5.0000"), total_tokens=10),
        TokenBudgetPolicy(total_cost_usd=Decimal("10"), total_tokens=100),
    )

    encoded = json.dumps(evaluation.to_payload(), ensure_ascii=False)
    assert '"5.0000"' in encoded
    assert evaluation.to_payload()["status"] == "OK"


@pytest.mark.parametrize(
    ("policy_kwargs", "message"),
    [
        ({"monthly_tokens": 0}, "monthly_tokens"),
        ({"monthly_tokens": -1}, "monthly_tokens"),
        ({"total_cost_usd": Decimal("0")}, "total_cost_usd"),
        ({"warning_ratio": 0}, "warning_ratio"),
        ({"warning_ratio": 1.5}, "warning_ratio"),
    ],
)
def test_invalid_policy_fails_explicitly(policy_kwargs: dict, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        TokenBudgetPolicy(**policy_kwargs)


def test_invalid_usage_fails_explicitly() -> None:
    with pytest.raises(DomainValidationError, match="daily_tokens"):
        BudgetUsage(daily_tokens=-5)
    with pytest.raises(DomainValidationError, match="monthly_cost_usd"):
        BudgetUsage(monthly_cost_usd=Decimal("-1"))  # type: ignore[arg-type]
