"""LLM-as-Judge 偏差校正与置信度路由测试（V52-006，蓝图 N3）。

**判定标准**：位置偏差必须被检出并降级；长度惩罚按蓝图数值；
三区路由的每条分支与理由可审计；输入顺序无关（同一语义 → 同一输出）。
"""

from __future__ import annotations

import pytest
from app.domain.annotation.judge_routing import (
    AUTO_APPROVE_THRESHOLD,
    CONFLICT_HUMAN_THRESHOLD,
    HUMAN_REVIEW_THRESHOLD,
    JudgeRoutingError,
    JudgeVerdict,
    RouteDecision,
    RoutingInput,
    SwapVerdict,
    apply_verbosity_penalty,
    blend_scores,
    correct_position_bias,
    is_length_ratio_safe,
    route_from_dual_pass,
    route_judge_result,
)


def _v(verdict: SwapVerdict, conf: float) -> JudgeVerdict:
    return JudgeVerdict(verdict=verdict, confidence=conf)


# ── 位置偏差校正（Swap & Average）──────────────────────────


def test_position_consistent_passes_fuse_confidence() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.9), backward=_v(SwapVerdict.B, 0.8))
    # backward 按反转呈现解读：槽 B == forward 语义的 canonical A → 一致
    assert result.canonical_winner is SwapVerdict.A
    assert result.fused_confidence == pytest.approx(0.85)
    assert result.position_consistent and not result.position_bias_detected


def test_position_bias_flip_degrades_to_tie_half_confidence() -> None:
    """两次结论互相翻转（真位置偏差）→ TIE + 置信度折半。"""
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.9), backward=_v(SwapVerdict.A, 0.9))
    # backward 呈现反转后槽 A == canonical B → 与 forward 冲突
    assert result.canonical_winner is SwapVerdict.TIE
    assert result.position_bias_detected
    assert result.fused_confidence == pytest.approx(0.45)


def test_double_tie_stays_tie_without_penalty() -> None:
    result = correct_position_bias(
        forward=_v(SwapVerdict.TIE, 0.7), backward=_v(SwapVerdict.TIE, 0.9)
    )
    assert result.canonical_winner is SwapVerdict.TIE
    assert result.fused_confidence == pytest.approx(0.8)
    assert not result.position_bias_detected


def test_exact_two_passes_required() -> None:
    # 单槽标签被槽校验先拦截（同一错误类型，消息不同）
    with pytest.raises(JudgeRoutingError, match="不同的槽标签"):
        correct_position_bias(
            forward=_v(SwapVerdict.A, 0.9), backward=_v(SwapVerdict.A, 0.9), presented_order=("A",)
        )
    with pytest.raises(JudgeRoutingError, match="不同的槽标签"):
        correct_position_bias(
            forward=_v(SwapVerdict.A, 0.9), backward=_v(SwapVerdict.A, 0.9), presented_order=()
        )


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(JudgeRoutingError, match="confidence"):
        _v(SwapVerdict.A, 1.5)
    with pytest.raises(JudgeRoutingError, match="不同的槽标签"):
        correct_position_bias(
            forward=_v(SwapVerdict.A, 0.5),
            backward=_v(SwapVerdict.A, 0.5),
            presented_order=("A", "A"),
        )


# ── 长度偏差惩罚 ─────────────────────────────────────────


def test_verbosity_penalty_applies_above_trigger_ratio() -> None:
    result = correct_position_bias(
        forward=_v(SwapVerdict.A, 0.95), backward=_v(SwapVerdict.B, 0.95)
    )
    # 500/100 = 5 倍 → 惩罚 (5-2)*0.05 = 0.15
    penalized = apply_verbosity_penalty(result, chosen_chars=500, other_chars=100)
    assert penalized.verbosity_penalty == pytest.approx(0.15)
    assert penalized.fused_confidence == pytest.approx(0.8)


def test_verbosity_penalty_skipped_for_small_ratio_and_tie() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.9), backward=_v(SwapVerdict.B, 0.9))
    assert (
        apply_verbosity_penalty(result, chosen_chars=120, other_chars=100).verbosity_penalty == 0.0
    )
    tie = correct_position_bias(forward=_v(SwapVerdict.TIE, 0.9), backward=_v(SwapVerdict.TIE, 0.9))
    assert apply_verbosity_penalty(tie, chosen_chars=500, other_chars=100).verbosity_penalty == 0.0


def test_verbosity_penalty_floor_and_invalid() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.6), backward=_v(SwapVerdict.B, 0.6))
    extreme = apply_verbosity_penalty(result, chosen_chars=100_000, other_chars=1)
    assert extreme.fused_confidence >= 0.0
    with pytest.raises(JudgeRoutingError):
        apply_verbosity_penalty(result, chosen_chars=0, other_chars=10)
    assert not is_length_ratio_safe(1000, 100)
    assert is_length_ratio_safe(110, 100)


# ── 三区路由 ─────────────────────────────────────────────


def _routed(winner: SwapVerdict, conf: float, human_value: str | None, **kw: object):
    result = correct_position_bias(forward=_v(winner, conf), backward=_v(SwapVerdict.TIE, conf))
    return route_judge_result(
        result,
        RoutingInput(judge_winner=winner, judge_confidence=conf, human_value=human_value, **kw),
    )  # type: ignore[arg-type]


def test_auto_approve_when_agree_and_high_confidence() -> None:
    # 修正：backward 用一致判决避免位置偏差
    result = correct_position_bias(
        forward=_v(SwapVerdict.A, 0.95), backward=_v(SwapVerdict.B, 0.95)
    )
    outcome = route_judge_result(
        result, RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.95, human_value="A")
    )
    assert outcome.decision is RouteDecision.AUTO_APPROVE
    assert any("≥ 0.92" in r for r in outcome.reasons)
    assert AUTO_APPROVE_THRESHOLD == 0.92


def test_position_bias_blocks_auto_approve() -> None:
    """即使一致高置信，检出位置偏差翻转时也不自动放行。"""
    flipped = correct_position_bias(
        forward=_v(SwapVerdict.A, 0.99), backward=_v(SwapVerdict.A, 0.99)
    )
    outcome = route_judge_result(
        flipped, RoutingInput(judge_winner=SwapVerdict.TIE, judge_confidence=0.5, human_value="TIE")
    )
    assert outcome.decision in (RouteDecision.HUMAN_REVIEW, RouteDecision.STANDARD_FLOW)
    assert outcome.decision is not RouteDecision.AUTO_APPROVE


def test_conflict_in_gray_zone_forces_human_review() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.7), backward=_v(SwapVerdict.B, 0.7))
    outcome = route_judge_result(
        result, RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.7, human_value="B")
    )
    assert outcome.decision is RouteDecision.HUMAN_REVIEW
    assert any("灰区" in r for r in outcome.reasons)
    assert CONFLICT_HUMAN_THRESHOLD == 0.5 and HUMAN_REVIEW_THRESHOLD == 0.85


def test_conflict_outside_gray_zone_standard_flow() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.3), backward=_v(SwapVerdict.B, 0.3))
    outcome = route_judge_result(
        result, RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.3, human_value="B")
    )
    assert outcome.decision is RouteDecision.STANDARD_FLOW


def test_agree_but_low_confidence_standard_flow() -> None:
    result = correct_position_bias(forward=_v(SwapVerdict.A, 0.5), backward=_v(SwapVerdict.B, 0.5))
    outcome = route_judge_result(
        result, RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.5, human_value="A")
    )
    assert outcome.decision is RouteDecision.STANDARD_FLOW


def test_tie_fused_routes_to_review_or_standard() -> None:
    # 位置偏差降级：conf 1.0 双判决 → fused = 1.0*0.5 = 0.5（灰区下界含）→ HUMAN
    flipped = correct_position_bias(forward=_v(SwapVerdict.A, 1.0), backward=_v(SwapVerdict.A, 1.0))
    review = route_judge_result(
        flipped, RoutingInput(judge_winner=SwapVerdict.TIE, judge_confidence=0.5, human_value=None)
    )
    assert review.decision is RouteDecision.HUMAN_REVIEW
    # 低置信 TIE（真实双平局）→ STANDARD
    low = correct_position_bias(forward=_v(SwapVerdict.TIE, 0.2), backward=_v(SwapVerdict.TIE, 0.2))
    std = route_judge_result(
        low, RoutingInput(judge_winner=SwapVerdict.TIE, judge_confidence=0.2, human_value=None)
    )
    assert std.decision is RouteDecision.STANDARD_FLOW


def test_not_comparable_never_auto_approves() -> None:
    result = correct_position_bias(
        forward=_v(SwapVerdict.A, 0.99), backward=_v(SwapVerdict.B, 0.99)
    )
    outcome = route_judge_result(
        result,
        RoutingInput(
            judge_winner=SwapVerdict.A, judge_confidence=0.99, human_value=None, comparable=False
        ),
    )
    assert outcome.decision is RouteDecision.HUMAN_REVIEW
    low = correct_position_bias(forward=_v(SwapVerdict.A, 0.2), backward=_v(SwapVerdict.B, 0.2))
    std = route_judge_result(
        low,
        RoutingInput(
            judge_winner=SwapVerdict.A, judge_confidence=0.2, human_value=None, comparable=False
        ),
    )
    assert std.decision is RouteDecision.STANDARD_FLOW


def test_every_outcome_carries_reasons_and_confidence() -> None:
    result = correct_position_bias(
        forward=_v(SwapVerdict.A, 0.95), backward=_v(SwapVerdict.B, 0.95)
    )
    outcome = route_judge_result(
        result, RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.95, human_value="A")
    )
    assert outcome.reasons and all(r.strip() for r in outcome.reasons)
    assert 0.0 <= outcome.confidence <= 1.0


def test_one_stop_entry_full_chain() -> None:
    outcome = route_from_dual_pass(
        forward=_v(SwapVerdict.A, 0.96),
        backward=_v(SwapVerdict.B, 0.96),
        human=RoutingInput(judge_winner=SwapVerdict.A, judge_confidence=0.96, human_value="A"),
        chosen_chars=600,
        other_chars=100,
    )
    # 位置一致 + 长度惩罚 (6-2)*0.05=0.2 → 0.96-0.2=0.76 < 0.92 → 标准流
    assert outcome.decision is RouteDecision.STANDARD_FLOW
    assert outcome.confidence == pytest.approx(0.76)


def test_blend_scores_mean() -> None:
    assert blend_scores([0.9, 0.8]) == pytest.approx(0.85)
    with pytest.raises(JudgeRoutingError):
        blend_scores([])
    with pytest.raises(JudgeRoutingError):
        blend_scores([float("nan")])
