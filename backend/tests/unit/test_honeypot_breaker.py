"""黄金题熔断器测试（V52-007，蓝图 N3 Honeypot）。

**判定标准**：连续 2 个满窗口准确率 <85% → PAUSED + 冻结结算；
小样本不误伤；PAUSE 可解除且窗口重置；判定确定性。
"""

from __future__ import annotations

import pytest
from app.domain.annotation.honeypot_breaker import (
    CONSECUTIVE_WINDOWS,
    PAUSE_THRESHOLD,
    BreakerState,
    HoneypotBreaker,
    HoneypotError,
    WindowStat,
)


def _feed(breaker: HoneypotBreaker, pattern: list[bool]) -> None:
    for hit in pattern:
        breaker.record_gold_result(hit=hit)


def test_threshold_constants_match_blueprint() -> None:
    assert PAUSE_THRESHOLD == 0.85
    assert CONSECUTIVE_WINDOWS == 2


def test_insufficient_samples_never_pauses() -> None:
    breaker = HoneypotBreaker(window_size=10, consecutive_windows=2, min_window=5)
    _feed(breaker, [False] * 9)  # 连一个满窗口都没有
    decision = breaker.evaluate()
    assert decision.state is BreakerState.NORMAL
    assert any("样本不足" in r for r in decision.reasons)


def test_one_bad_window_not_enough_to_pause() -> None:
    breaker = HoneypotBreaker(window_size=10, consecutive_windows=2)
    _feed(breaker, [False] * 10 + [True] * 10)  # 第一窗 0%，第二窗 100%
    decision = breaker.evaluate()
    assert decision.state is BreakerState.NORMAL
    assert decision.window_accuracies == (0.0, 1.0)


def test_two_consecutive_bad_windows_pause_and_freeze() -> None:
    breaker = HoneypotBreaker(window_size=10, consecutive_windows=2)
    _feed(
        breaker, [False] * 10 + [False, True, False, True, False, False, False, False, False, False]
    )  # 0% + 20%
    decision = breaker.evaluate()
    assert decision.is_paused and decision.freeze_settlement
    assert any("低于 0.85" in r or "低于 85%" in r for r in decision.reasons)
    # PAUSED 后再评估保持暂停
    again = breaker.evaluate()
    assert again.is_paused
    with pytest.raises(HoneypotError, match="PAUSED"):
        breaker.record_gold_result(hit=True)


def test_exactly_at_threshold_does_not_pause() -> None:
    """窗口 90%（9/10）不低于 85% → 不触发；80% 连续两窗 → 触发（边界验证）。"""
    breaker = HoneypotBreaker(window_size=10, consecutive_windows=2)
    _feed(breaker, [True] * 9 + [False] + [True] * 9 + [False])
    decision = breaker.evaluate()
    assert decision.state is BreakerState.NORMAL
    # 边界另一侧：80% < 85%
    breaker2 = HoneypotBreaker(window_size=10, consecutive_windows=2)
    _feed(breaker2, [True] * 8 + [False] * 2 + [True] * 8 + [False] * 2)
    assert breaker2.evaluate().is_paused


def test_resume_resets_windows() -> None:
    breaker = HoneypotBreaker(window_size=10, consecutive_windows=2)
    _feed(breaker, [False] * 20)
    assert breaker.evaluate().is_paused
    decision = breaker.resume(by="admin-1")
    assert decision.state is BreakerState.NORMAL
    _feed(breaker, [True] * 10)
    assert not breaker.evaluate().is_paused
    with pytest.raises(HoneypotError, match="未暂停"):
        breaker.resume(by="admin-1")


def test_custom_threshold_and_window_validation() -> None:
    with pytest.raises(HoneypotError, match="window_size"):
        HoneypotBreaker(window_size=4, min_window=5)
    with pytest.raises(HoneypotError, match="pause_threshold"):
        HoneypotBreaker(pause_threshold=0.0)
    with pytest.raises(HoneypotError, match="consecutive_windows"):
        HoneypotBreaker(consecutive_windows=0)


def test_windows_snapshot_pure_view() -> None:
    breaker = HoneypotBreaker(window_size=5)
    windows = breaker.windows_snapshot([True, True, False, True, True, False] * 2)
    assert len(windows) == 3
    assert windows[0].total == 5
    with pytest.raises(HoneypotError):
        WindowStat(hits=3, total=2)


def test_evaluate_is_deterministic() -> None:
    pattern = [False] * 10 + [False] * 10
    a = HoneypotBreaker()
    _feed(a, pattern)
    b = HoneypotBreaker()
    _feed(b, pattern)
    assert a.evaluate().state is b.evaluate().state
    assert a.evaluate().reasons == b.evaluate().reasons
