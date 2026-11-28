"""Judge 校准集一致性与漂移评估测试（V61-017）。

钉住四条语义：

1. 不可比样本不进分母；
2. 单方 TIE 不算对（保守口径）；
3. 退化分布（只判一类）kappa 返回 ``None`` 而不是 1.0；
4. 样本不足时不下漂移结论。
"""

from __future__ import annotations

import pytest
from app.domain.annotation.judge_calibration import (
    DEFAULT_MAX_KAPPA_DROP,
    CalibrationSample,
    DriftVerdict,
    detect_drift,
    evaluate_calibration,
)
from app.domain.annotation.judge_routing import JudgeRoutingError, SwapVerdict


def _samples(
    pairs: list[tuple[SwapVerdict, SwapVerdict]], *, comparable: bool = True
) -> list[CalibrationSample]:
    return [
        CalibrationSample(judge_verdict=j, human_verdict=h, comparable=comparable) for j, h in pairs
    ]


def test_accuracy_and_kappa_on_perfect_agreement() -> None:
    pairs = [
        (SwapVerdict.A, SwapVerdict.A),
        (SwapVerdict.B, SwapVerdict.B),
        (SwapVerdict.TIE, SwapVerdict.TIE),
        (SwapVerdict.A, SwapVerdict.A),
    ]
    report = evaluate_calibration(_samples(pairs))

    assert report.total_samples == 4
    assert report.comparable_samples == 4
    assert report.correct == 4
    assert report.accuracy == 1.0
    assert report.kappa == 1.0


def test_incomparable_samples_are_excluded_from_denominator() -> None:
    samples = _samples([(SwapVerdict.A, SwapVerdict.A)]) + _samples(
        [(SwapVerdict.A, SwapVerdict.B)], comparable=False
    )
    report = evaluate_calibration(samples)

    assert report.total_samples == 2
    assert report.comparable_samples == 1
    assert report.correct == 1
    assert report.accuracy == 1.0


def test_one_sided_tie_counts_as_wrong() -> None:
    report = evaluate_calibration(_samples([(SwapVerdict.TIE, SwapVerdict.A)]))

    assert report.correct == 0
    assert report.accuracy == 0.0


def test_degenerate_single_class_output_has_no_kappa() -> None:
    """judge 全判 A、人工也是 A → 准确率 1.0 但 kappa 不可计算（返回 None）。"""
    pairs = [(SwapVerdict.A, SwapVerdict.A)] * 10
    report = evaluate_calibration(_samples(pairs))

    assert report.accuracy == 1.0
    assert report.kappa is None
    assert report.is_degenerate is True


def test_empty_calibration_returns_none_metrics() -> None:
    report = evaluate_calibration([])

    assert report.comparable_samples == 0
    assert report.accuracy is None
    assert report.kappa is None
    assert report.is_degenerate is False


def test_drift_insufficient_data_does_not_conclude() -> None:
    # 用非退化的小样本：两类都出现，kappa 可计算，只是样本量不够。
    baseline = _mixed(6, correct_every=3)
    current = _mixed(6, correct_every=2)

    report = detect_drift(baseline, current, min_samples=20)

    assert report.verdict is DriftVerdict.INSUFFICIENT_DATA
    assert any("可比样本不足" in reason for reason in report.reasons)
    assert report.kappa_delta is not None  # 仍然如实给出指标，只是不下结论


def _mixed(n: int, *, correct_every: int) -> list[CalibrationSample]:
    """构造近似混合分布：每 correct_every 条里 1 条判错，其余正确。"""
    pairs: list[tuple[SwapVerdict, SwapVerdict]] = []
    for i in range(n):
        if (i + 1) % correct_every == 0:
            pairs.append((SwapVerdict.A, SwapVerdict.B))
        else:
            pairs.append(
                (SwapVerdict.A, SwapVerdict.A) if i % 2 == 0 else (SwapVerdict.B, SwapVerdict.B)
            )
    return _samples(pairs)


def test_detects_drift_when_kappa_drops_beyond_threshold() -> None:
    baseline = _mixed(40, correct_every=40)  # 几乎全对
    current = _mixed(40, correct_every=2)  # 一半判错

    report = detect_drift(baseline, current, min_samples=20)

    assert report.verdict is DriftVerdict.DRIFTED
    assert report.kappa_delta is not None and report.kappa_delta < -DEFAULT_MAX_KAPPA_DROP
    assert any("kappa 下降" in reason for reason in report.reasons)


def test_stable_when_change_within_threshold() -> None:
    baseline = _mixed(40, correct_every=4)
    current = _mixed(40, correct_every=4)

    report = detect_drift(baseline, current, min_samples=20)

    assert report.verdict is DriftVerdict.STABLE
    assert report.kappa_delta == 0.0


def test_improved_when_kappa_rises_beyond_threshold() -> None:
    baseline = _mixed(40, correct_every=2)
    current = _mixed(40, correct_every=40)

    report = detect_drift(baseline, current, min_samples=20)

    assert report.verdict is DriftVerdict.IMPROVED


def test_rejects_negative_threshold_and_bad_min_samples() -> None:
    samples = _mixed(30, correct_every=4)
    with pytest.raises(JudgeRoutingError, match="max_kappa_drop"):
        detect_drift(samples, samples, max_kappa_drop=-0.1)
    with pytest.raises(JudgeRoutingError, match="min_samples"):
        detect_drift(samples, samples, min_samples=0)


def test_sample_rejects_non_verdict() -> None:
    with pytest.raises(JudgeRoutingError, match="judge_verdict"):
        CalibrationSample(judge_verdict="A", human_verdict=SwapVerdict.A)  # type: ignore[arg-type]


def test_drift_payload_is_json_safe() -> None:
    import json

    baseline = _mixed(40, correct_every=4)
    current = _mixed(40, correct_every=40)
    payload = detect_drift(baseline, current, min_samples=20).to_payload()

    json.dumps(payload, ensure_ascii=False)
    assert payload["verdict"] == "IMPROVED"
    assert payload["baseline"]["total_samples"] == 40
