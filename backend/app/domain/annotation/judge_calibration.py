"""LLM-as-Judge 校准集一致性与漂移评估（V61-017，蓝图 N3）。

## 缺口与定位

``judge_routing`` 已解决「单条判决的偏差校正与三区路由」，但没有任何
**跨时间的一致性度量**：

- 黄金校准集（人工已确认的 ground truth）上 judge 的准确率是多少？
- 本周相对上周是否显著下降（漂移）？
- 平局/不可比样本如何计入？

没有这两件事实，「judge 还准不准」只能靠感觉；模型换版或提示词变更后
无人能证明质量没有退化。

## 口径

- **准确率**：``correct / comparable``；不可比样本（``comparable=False``）
  不计入分子分母，但单独计数——把它们混进准确率会让「没判」看起来像「判错」；
- **平局处理**：judge 与人工都是 ``TIE`` 才算对；一方 ``TIE`` 另一方不是则算错
  （宁可保守，不把模糊判决算成正确）；
- **Kappa**：Cohen's kappa 在 3 类（A/B/TIE）上计算，扣除随机一致率——
  「全判 A」这种退化策略准确率可能不低但 kappa 接近 0，必须能看出来；
- **漂移**：``current_kappa - baseline_kappa < -max_drop`` 判 ``DRIFTED``；
  任一集合样本量低于 ``min_samples`` 判 ``INSUFFICIENT_DATA``，不据此下结论。

本模块纯领域、无 IO、无时钟依赖（时间窗由调用方切分后传入）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from app.domain.annotation.judge_routing import JudgeRoutingError, SwapVerdict

#: 少于该样本数不下漂移结论（统计学上不可靠）。
DEFAULT_MIN_SAMPLES = 20
#: kappa 下降超过该幅度判漂移（蓝图未给数值，取保守默认，可由调用方覆盖）。
DEFAULT_MAX_KAPPA_DROP = 0.10


class DriftVerdict(StrEnum):
    """漂移评估结论。"""

    STABLE = "STABLE"
    DRIFTED = "DRIFTED"
    IMPROVED = "IMPROVED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """一条校准样本：人工 ground truth 与 judge 判决的配对。"""

    judge_verdict: SwapVerdict
    human_verdict: SwapVerdict
    #: 人类标注是否与 judge 判决可比；False 表示人工未标/维度不同，不计入分母
    comparable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.judge_verdict, SwapVerdict):
            raise JudgeRoutingError("judge_verdict 必须是 SwapVerdict")
        if not isinstance(self.human_verdict, SwapVerdict):
            raise JudgeRoutingError("human_verdict 必须是 SwapVerdict")
        if not isinstance(self.comparable, bool):
            raise JudgeRoutingError("comparable 必须是布尔值")


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """单个校准集的一致性报告。"""

    total_samples: int
    comparable_samples: int
    correct: int
    accuracy: float | None
    kappa: float | None
    judge_distribution: dict[str, int]
    human_distribution: dict[str, int]

    @property
    def is_degenerate(self) -> bool:
        """judge 只输出单一类别（kappa 无意义且通常接近 0）。"""
        return len(self.judge_distribution) == 1 and self.comparable_samples > 0

    def to_payload(self) -> dict[str, object]:
        return {
            "total_samples": self.total_samples,
            "comparable_samples": self.comparable_samples,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "kappa": self.kappa,
            "judge_distribution": dict(sorted(self.judge_distribution.items())),
            "human_distribution": dict(sorted(self.human_distribution.items())),
            "is_degenerate": self.is_degenerate,
        }


@dataclass(frozen=True, slots=True)
class DriftReport:
    """两个时间窗的一致性漂移结论。"""

    verdict: DriftVerdict
    baseline: CalibrationReport
    current: CalibrationReport
    kappa_delta: float | None
    accuracy_delta: float | None
    max_kappa_drop: float
    min_samples: int
    reasons: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "baseline": self.baseline.to_payload(),
            "current": self.current.to_payload(),
            "kappa_delta": self.kappa_delta,
            "accuracy_delta": self.accuracy_delta,
            "max_kappa_drop": self.max_kappa_drop,
            "min_samples": self.min_samples,
            "reasons": list(self.reasons),
        }


def _distribution(samples: list[SwapVerdict]) -> dict[str, int]:
    counter = Counter(item.value for item in samples)
    return dict(counter)


def _cohen_kappa(samples: list[CalibrationSample]) -> float | None:
    """3 类 Cohen's kappa；退化情形（任一方只有单一类别且一致率 1.0）返回 None。"""
    if not samples:
        return None
    n = len(samples)
    observed = sum(1 for item in samples if item.judge_verdict is item.human_verdict) / n
    judge_counter = Counter(item.judge_verdict for item in samples)
    human_counter = Counter(item.human_verdict for item in samples)
    expected = sum(
        (judge_counter[cat] / n) * (human_counter[cat] / n) for cat in set(judge_counter)
    )
    if expected >= 1.0:
        # judge 与人工都只用一个类别：kappa 数学上 0/0，返回 None 而不是 1.0
        # ——那会掩盖"什么都没评判"。
        return None
    return round((observed - expected) / (1.0 - expected), 6)


def evaluate_calibration(samples: list[CalibrationSample]) -> CalibrationReport:
    """计算单个校准集的准确率与 kappa。"""
    comparable = [item for item in samples if item.comparable]
    correct = sum(
        1
        for item in comparable
        if item.judge_verdict is item.human_verdict
        or (item.judge_verdict is SwapVerdict.TIE and item.human_verdict is SwapVerdict.TIE)
    )
    accuracy = round(correct / len(comparable), 6) if comparable else None
    return CalibrationReport(
        total_samples=len(samples),
        comparable_samples=len(comparable),
        correct=correct,
        accuracy=accuracy,
        kappa=_cohen_kappa(comparable),
        judge_distribution=_distribution([item.judge_verdict for item in comparable]),
        human_distribution=_distribution([item.human_verdict for item in comparable]),
    )


def detect_drift(
    baseline_samples: list[CalibrationSample],
    current_samples: list[CalibrationSample],
    *,
    max_kappa_drop: float = DEFAULT_MAX_KAPPA_DROP,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> DriftReport:
    """比较两个时间窗的一致性，判定 ``STABLE`` / ``DRIFTED`` / ``IMPROVED``。"""
    if max_kappa_drop < 0:
        raise JudgeRoutingError("max_kappa_drop 不能为负")
    if min_samples < 1:
        raise JudgeRoutingError("min_samples 必须为正整数")

    baseline = evaluate_calibration(baseline_samples)
    current = evaluate_calibration(current_samples)

    kappa_delta: float | None = None
    accuracy_delta: float | None = None
    if baseline.kappa is not None and current.kappa is not None:
        kappa_delta = round(current.kappa - baseline.kappa, 6)
    if baseline.accuracy is not None and current.accuracy is not None:
        accuracy_delta = round(current.accuracy - baseline.accuracy, 6)

    reasons: list[str] = []
    if baseline.comparable_samples < min_samples or current.comparable_samples < min_samples:
        reasons.append(
            f"可比样本不足（基线 {baseline.comparable_samples} / 当前 "
            f"{current.comparable_samples}，门槛 {min_samples}）"
        )
        return DriftReport(
            verdict=DriftVerdict.INSUFFICIENT_DATA,
            baseline=baseline,
            current=current,
            kappa_delta=kappa_delta,
            accuracy_delta=accuracy_delta,
            max_kappa_drop=max_kappa_drop,
            min_samples=min_samples,
            reasons=tuple(reasons),
        )

    if kappa_delta is None:
        reasons.append("kappa 不可计算（类别分布退化），仅按准确率比较")
        if accuracy_delta is not None and accuracy_delta < -max_kappa_drop:
            return DriftReport(
                verdict=DriftVerdict.DRIFTED,
                baseline=baseline,
                current=current,
                kappa_delta=None,
                accuracy_delta=accuracy_delta,
                max_kappa_drop=max_kappa_drop,
                min_samples=min_samples,
                reasons=tuple(reasons),
            )
        reasons.append("准确率未超阈值")
        return DriftReport(
            verdict=DriftVerdict.STABLE,
            baseline=baseline,
            current=current,
            kappa_delta=None,
            accuracy_delta=accuracy_delta,
            max_kappa_drop=max_kappa_drop,
            min_samples=min_samples,
            reasons=tuple(reasons),
        )

    if kappa_delta < -max_kappa_drop:
        reasons.append(f"kappa 下降 {abs(kappa_delta):.4f}，超过阈值 {max_kappa_drop}")
        verdict = DriftVerdict.DRIFTED
    elif kappa_delta > max_kappa_drop:
        reasons.append(f"kappa 提升 {kappa_delta:.4f}，超过阈值 {max_kappa_drop}")
        verdict = DriftVerdict.IMPROVED
    else:
        reasons.append(f"kappa 变化 {kappa_delta:+.4f}，在阈值 ±{max_kappa_drop} 内")
        verdict = DriftVerdict.STABLE

    return DriftReport(
        verdict=verdict,
        baseline=baseline,
        current=current,
        kappa_delta=kappa_delta,
        accuracy_delta=accuracy_delta,
        max_kappa_drop=max_kappa_drop,
        min_samples=min_samples,
        reasons=tuple(reasons),
    )


__all__ = [
    "DEFAULT_MAX_KAPPA_DROP",
    "DEFAULT_MIN_SAMPLES",
    "CalibrationReport",
    "CalibrationSample",
    "DriftReport",
    "DriftVerdict",
    "detect_drift",
    "evaluate_calibration",
]
