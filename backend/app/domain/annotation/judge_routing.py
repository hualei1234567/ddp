"""LLM-as-Judge 偏差校正与置信度路由（V52-006，蓝图 N3）。

**蓝图要求**（N3「LLM-as-Judge 工程化防坑要点」）：
1. **位置偏差校正（Position Bias）**：裁判偏爱第一个回答——用 Swap & Average
   （正反交换位置各判一次）消除；
2. **长度偏差（Verbosity Bias）**：裁判偏爱长回答——加长度惩罚项；
3. **置信度三区路由**：judge 与人工一致且置信度 >0.92 → 自动放行免二审；
   灰区 0.5–0.85 → 强制人工复核；其余走标准流程。

**本模块是纯领域层**：不触 DB、不触 API、不建迁移——只提供确定性判定函数，
由上层（Judge 调用方 / review 用例）在既有流程上**可选接入**。所有阈值
显式可配，默认值来自蓝图数值。

设计对齐仓库既有约束：
- 偏好语义 = JSON 约定值（MEM-DEC-002），winner ∈ {A,B,TIE} 指呈现槽；
- 偏差校正产出 = 两次独立判决的融合结论，输入顺序无关（同一语义 → 同一输出）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

#: 蓝图 N3 数值：高置信自动放行阈值
AUTO_APPROVE_THRESHOLD = 0.92
#: 蓝图 N3 数值：灰区下界（<= 该值强制人工）
HUMAN_REVIEW_THRESHOLD = 0.85
#: 灰区上界（judge 与人工冲突且置信度低于该值走人工）
CONFLICT_HUMAN_THRESHOLD = 0.5
#: 长度惩罚强度：长度比每超出 1 倍扣减的置信度比例
VERBOSITY_PENALTY_RATE = 0.05
#: 长度比（长/短）达到该值才开始惩罚（短差异不惩罚）
VERBOSITY_TRIGGER_RATIO = 2.0


class JudgeRoutingError(ValueError):
    """Judge 路由的领域错误。"""


class RouteDecision(StrEnum):
    """三区路由结论。"""

    AUTO_APPROVE = "AUTO_APPROVE"  # 与人工一致且高置信 → 免二审
    HUMAN_REVIEW = "HUMAN_REVIEW"  # 灰区/冲突 → 资深质检
    STANDARD_FLOW = "STANDARD_FLOW"  # 其余 → 既有抽检/双盲流程


class SwapVerdict(StrEnum):
    """单次（带位置）偏好判决。"""

    A = "A"  # 裁判选了呈现槽 A
    B = "B"  # 裁判选了呈现槽 B
    TIE = "TIE"  # 平局


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """一次带元数据的裁判判决。"""

    verdict: SwapVerdict
    confidence: float
    rationale: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, SwapVerdict):
            raise JudgeRoutingError("verdict 必须是 SwapVerdict")
        if not (0.0 <= self.confidence <= 1.0):
            raise JudgeRoutingError("confidence 必须在 [0,1]")


@dataclass(frozen=True, slots=True)
class BiasCorrectedResult:
    """Swap & Average 校正后的融合结论。"""

    #: canonical 胜者：以**输入顺序**的 canonical A/B 语义表达（与呈现无关）；
    #: TIE 保持平局。
    canonical_winner: SwapVerdict
    #: 融合置信度（两次判决置信度均值，再扣位置不一致惩罚）
    fused_confidence: float
    #: 两次判决是否指向同一 canonical 胜者；False = 位置偏差翻转
    position_consistent: bool
    #: 位置偏差检出（两次结论不同且非双 TIE）
    position_bias_detected: bool
    #: 长度惩罚扣减量（0 = 未触发）
    verbosity_penalty: float
    #: 两次原始判决（审计用）
    passes: tuple[JudgeVerdict, JudgeVerdict] = field(default_factory=tuple)


def _canonical(winner_slot: SwapVerdict, presented_order: tuple[str, str]) -> SwapVerdict:
    """把呈现槽标签映射回 canonical（槽 → canonical 响应名）。"""
    mapping = dict(zip(("A", "B"), presented_order, strict=False))
    if winner_slot is SwapVerdict.TIE:
        return SwapVerdict.TIE
    if winner_slot.value not in mapping:
        raise JudgeRoutingError(f"呈现槽标签非法: {winner_slot!r}")
    return SwapVerdict(mapping[winner_slot])


def _confidence_after_swap(
    passes: Sequence[JudgeVerdict], presented_order: tuple[str, str]
) -> tuple[SwapVerdict, float, bool]:
    """按 canonical 语义融合两次判决。

    规则：
    - 两次 canonical 结论一致 → 置信度取均值；
    - 两次结论不一致（位置偏差）→ 结论降级为 TIE（不可信），置信度取均值×0.5；
    - 双 TIE → TIE。
    """
    if len(passes) != 2:
        raise JudgeRoutingError("位置校正必须提供恰好 2 次判决（正/反各一次）")
    canonical_a = _canonical(passes[0].verdict, presented_order)
    canonical_b = _canonical(passes[1].verdict, presented_order)
    base_conf = (passes[0].confidence + passes[1].confidence) / 2.0
    if canonical_a is SwapVerdict.TIE and canonical_b is SwapVerdict.TIE:
        return SwapVerdict.TIE, base_conf, True
    if canonical_a is canonical_b:
        return canonical_a, base_conf, True
    # 位置翻转：判定不可信 → TIE + 置信度折半（蓝图 position bias 处置）
    return SwapVerdict.TIE, base_conf * 0.5, False


def correct_position_bias(
    *,
    forward: JudgeVerdict,
    backward: JudgeVerdict,
    presented_order: tuple[str, str] = ("A", "B"),
) -> BiasCorrectedResult:
    """Swap & Average：同一对回答正反两个呈现顺序各判一次后融合。

    ``forward``：按 ``presented_order`` 呈现时的判决；
    ``backward``：交换两回答位置后（即呈现顺序反转）的判决。
    两次判决的**槽标签**都按各自那次呈现的槽语义解读；调用方必须保证
    backward 的槽标签是反转后呈现的（本函数只做 canonical 归一与融合，
    不重排输入——重排由调用方在发起第二次推理时完成）。

    因此这里按 "canonical 对 = 反转呈现顺序" 解读 backward：
    backward.pass 中槽 X 里的回答，对应 forward 语义的另一槽。
    """
    if len(presented_order) != 2 or presented_order[0] == presented_order[1]:
        raise JudgeRoutingError("presented_order 必须是两个不同的槽标签")
    # backward 呈现顺序 = forward 的反转
    backward_order = (presented_order[1], presented_order[0])
    canonical_a = _canonical(forward.verdict, presented_order)
    canonical_b = _canonical(backward.verdict, backward_order)
    base_conf = (forward.confidence + backward.confidence) / 2.0

    position_consistent = True
    if canonical_a is SwapVerdict.TIE and canonical_b is SwapVerdict.TIE:
        winner, fused = SwapVerdict.TIE, base_conf
    elif canonical_a is canonical_b:
        winner, fused = canonical_a, base_conf
    else:
        # 位置偏差：两次结论互相翻转 → 判定不可信，降级 TIE
        position_consistent = False
        winner, fused = SwapVerdict.TIE, base_conf * 0.5

    return BiasCorrectedResult(
        canonical_winner=winner,
        fused_confidence=round(fused, 6),
        position_consistent=position_consistent,
        position_bias_detected=not position_consistent,
        verbosity_penalty=0.0,
        passes=(forward, backward),
    )


def apply_verbosity_penalty(
    result: BiasCorrectedResult,
    *,
    chosen_chars: int,
    other_chars: int,
) -> BiasCorrectedResult:
    """长度偏差惩罚：chosen 显著长于 other 时按比例扣置信度。

    蓝图 N3：裁判偏爱长回答。短差异不惩罚（避免伤害正常详尽回答），
    长度比 ≥ ``VERBOSITY_TRIGGER_RATIO`` 才生效，惩罚与超出倍数线性相关。
    """
    if chosen_chars < 0 or other_chars < 0:
        raise JudgeRoutingError("字符数不能为负")
    if result.canonical_winner is SwapVerdict.TIE:
        return result  # 平局无 chosen，不惩罚
    shorter = min(chosen_chars, other_chars)
    if shorter == 0:
        raise JudgeRoutingError("字符数必须 >0")
    ratio = chosen_chars / shorter
    if ratio <= VERBOSITY_TRIGGER_RATIO:
        return result
    penalty = min(0.5, (ratio - VERBOSITY_TRIGGER_RATIO) * VERBOSITY_PENALTY_RATE)
    fused = max(0.0, result.fused_confidence - penalty)
    return BiasCorrectedResult(
        canonical_winner=result.canonical_winner,
        fused_confidence=round(fused, 6),
        position_consistent=result.position_consistent,
        position_bias_detected=result.position_bias_detected,
        verbosity_penalty=round(penalty, 6),
        passes=result.passes,
    )


def is_length_ratio_safe(chosen_chars: int, other_chars: int) -> bool:
    """给调用方的快速预判：是否会被长度惩罚。"""
    if chosen_chars <= 0 or other_chars <= 0:
        return False
    ratio = max(chosen_chars, other_chars) / min(chosen_chars, other_chars)
    return ratio <= VERBOSITY_TRIGGER_RATIO


@dataclass(frozen=True, slots=True)
class RoutingInput:
    """路由输入：校正后的 judge 结论 + 人工标注结论。"""

    judge_winner: SwapVerdict
    judge_confidence: float
    #: 人工标注值（与 judge 比较的语义值；None = 人工未标/不可比）
    human_value: object | None
    #: 人工标注与 judge 可比的对象（如偏好记录的 canonical 语义）
    comparable: bool = True

    def __post_init__(self) -> None:
        if not (0.0 <= self.judge_confidence <= 1.0):
            raise JudgeRoutingError("judge_confidence 必须在 [0,1]")


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    """路由输出：结论 + 可解释依据（审计要求每条可追溯）。"""

    decision: RouteDecision
    reasons: tuple[str, ...]
    #: 自动放行时的融合置信度（审计留痕）
    confidence: float

    def __post_init__(self) -> None:
        if self.reasons and not all(r.strip() for r in self.reasons):
            raise JudgeRoutingError("reasons 存在空项")


def route_judge_result(
    outcome: BiasCorrectedResult,
    human: RoutingInput,
) -> RoutingOutcome:
    """三区路由（蓝图 N3）：

    1. judge 与人工一致 且 fused_confidence ≥ 0.92 → ``AUTO_APPROVE``；
    2. judge 与人工冲突 且 confidence ∈ [0.5, 0.85) 灰区 → ``HUMAN_REVIEW``；
    3. 其余（含低置信一致、高置信冲突、TIE）→ ``STANDARD_FLOW``。

    ``comparable=False``（如自由文本无自动可比性）时永不自动放行。
    """
    reasons: list[str] = []
    conf = outcome.fused_confidence

    # 可比性优先：不可比永不自动放行
    if not human.comparable:
        reasons.append("人工与 judge 结论不可自动比较，不满足自动放行前提")
        if conf < CONFLICT_HUMAN_THRESHOLD:
            reasons.append(f"置信度 {conf:.2f} 低于 {CONFLICT_HUMAN_THRESHOLD}，走标准流")
            return RoutingOutcome(RouteDecision.STANDARD_FLOW, tuple(reasons), conf)
        reasons.append(f"置信度 {conf:.2f} 落入复核区间，强制人工复核")
        return RoutingOutcome(RouteDecision.HUMAN_REVIEW, tuple(reasons), conf)

    agree = human.human_value is not None and (human.human_value == outcome.canonical_winner.value)
    if agree and conf >= AUTO_APPROVE_THRESHOLD:
        reasons.append(
            f"judge 与人工结论一致（{outcome.canonical_winner.value}）且置信度 "
            f"{conf:.2f} ≥ {AUTO_APPROVE_THRESHOLD}"
        )
        if outcome.position_bias_detected:
            reasons.append("存在位置偏差翻转，不自动放行")
            return RoutingOutcome(RouteDecision.HUMAN_REVIEW, tuple(reasons), conf)
        reasons.append("自动放行（免二审）")
        return RoutingOutcome(RouteDecision.AUTO_APPROVE, tuple(reasons), conf)

    if not agree and human.human_value is not None:
        reasons.append(
            f"judge（{outcome.canonical_winner.value}）与人工（{human.human_value}）结论冲突"
        )
        if HUMAN_REVIEW_THRESHOLD >= conf >= CONFLICT_HUMAN_THRESHOLD:
            reasons.append(
                f"冲突且置信度 {conf:.2f} 落入灰区 [{CONFLICT_HUMAN_THRESHOLD},"
                f"{HUMAN_REVIEW_THRESHOLD})，强制资深质检复核"
            )
            return RoutingOutcome(RouteDecision.HUMAN_REVIEW, tuple(reasons), conf)
        reasons.append("冲突但置信度不在灰区，走标准流（既有抽检/双盲规则接管）")
        return RoutingOutcome(RouteDecision.STANDARD_FLOW, tuple(reasons), conf)

    # 其余：一致但置信度不足 / 人工为空 / TIE
    if outcome.canonical_winner is SwapVerdict.TIE:
        reasons.append("judge 融合结论为 TIE（含位置偏差降级），不自动放行")
        if conf < CONFLICT_HUMAN_THRESHOLD:
            reasons.append(f"置信度 {conf:.2f} 过低，走标准流")
            return RoutingOutcome(RouteDecision.STANDARD_FLOW, tuple(reasons), conf)
        reasons.append(f"置信度 {conf:.2f} 落入复核区间，强制人工复核")
        return RoutingOutcome(RouteDecision.HUMAN_REVIEW, tuple(reasons), conf)
    reasons.append(
        f"judge 与人工{'' if human.human_value is None else '一致但'}置信度 "
        f"{conf:.2f} < {AUTO_APPROVE_THRESHOLD}，走标准流"
    )
    return RoutingOutcome(RouteDecision.STANDARD_FLOW, tuple(reasons), conf)


def route_from_dual_pass(
    *,
    forward: JudgeVerdict,
    backward: JudgeVerdict,
    human: RoutingInput,
    chosen_chars: int | None = None,
    other_chars: int | None = None,
    presented_order: tuple[str, str] = ("A", "B"),
) -> RoutingOutcome:
    """一站式入口：位置校正 →（可选）长度惩罚 → 三区路由。"""
    result = correct_position_bias(
        forward=forward, backward=backward, presented_order=presented_order
    )
    if chosen_chars is not None and other_chars is not None:
        result = apply_verbosity_penalty(result, chosen_chars=chosen_chars, other_chars=other_chars)
    return route_judge_result(result, human)


def blend_scores(scores: Sequence[float]) -> float:
    """多裁判分数融合（简单均值；离群剔除留给上层策略）。"""
    if not scores:
        raise JudgeRoutingError("至少需要 1 个分数")
    if any(not math.isfinite(s) for s in scores):
        raise JudgeRoutingError("分数必须有限")
    return round(sum(scores) / len(scores), 6)


__all__ = [
    "AUTO_APPROVE_THRESHOLD",
    "BiasCorrectedResult",
    "CONFLICT_HUMAN_THRESHOLD",
    "HUMAN_REVIEW_THRESHOLD",
    "JudgeRoutingError",
    "JudgeVerdict",
    "RouteDecision",
    "RoutingInput",
    "RoutingOutcome",
    "SwapVerdict",
    "VERBOSITY_PENALTY_RATE",
    "VERBOSITY_TRIGGER_RATIO",
    "apply_verbosity_penalty",
    "blend_scores",
    "correct_position_bias",
    "is_length_ratio_safe",
    "route_from_dual_pass",
    "route_judge_result",
]
