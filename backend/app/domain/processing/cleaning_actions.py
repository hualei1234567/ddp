"""``DATA_CLEANING`` 确定性 TRANSFORM 与 QUARANTINE 动作引擎（V61-015 / V61-016）。

## 设计原则（严守原始资产零篡改红线）

1. **原始资产永不可变**：本模块只接收只读 ``CleaningStreamSlice``，返回新的
   衍生切片与隔离决策；绝不修改输入切片、``RawAsset`` 或上游 ``STREAM_EXTRACTION``
   产物字节。
2. **QUARANTINE 显式留痕**：命中隔离规则的 ``target_ref`` 不会静默丢弃，而是
   产出结构化 ``QuarantineDecision``（含触发规则 ID、来源引用与证据摘要），
   供上层流水线阻断其进入数据集或人工复核恢复。
3. **TRANSFORM 可追溯且有界**：目前仅支持确定性动作单步幅值限幅
   （``CLN-NUM-003``：将相邻帧动作步长裁剪至 ``±max_action_step_delta``），
   每条衍生切片携带 ``transform_provenance`` 记录规则版本与裁剪计数。
4. **纯领域无 IO**：相同输入 + 相同规则/阈值配置必然得到完全相同的输出。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from app.domain.processing.cleaning_evaluators import RULE_ACTION_STEP_JUMP
from app.domain.processing.cleaning_rules import (
    CleaningAction,
    CleaningEvaluationReport,
    CleaningFrameSample,
    CleaningRuleHit,
    CleaningStreamSlice,
    CleaningThresholds,
)
from app.domain.processing.errors import DomainValidationError

TRANSFORM_SCHEMA_VERSION = "ddp.cleaning.transformed-stream/1.0.0"
SUPPORTED_TRANSFORM_RULE_IDS: frozenset[str] = frozenset({RULE_ACTION_STEP_JUMP})


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    """单个流切片的隔离裁决记录。"""

    target_ref: str
    source_ref: str | None
    rule_ids: tuple[str, ...]
    hit_count: int
    evidence_summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "target_ref": self.target_ref,
            "source_ref": self.source_ref,
            "rule_ids": list(self.rule_ids),
            "hit_count": self.hit_count,
            "evidence_summary": self.evidence_summary,
        }


@dataclass(frozen=True, slots=True)
class TransformedSlice:
    """TRANSFORM 产出的衍生流切片（不篡改原始切片）。"""

    target_ref: str
    source_ref: str | None
    observations: tuple[CleaningFrameSample, ...]
    actions: tuple[CleaningFrameSample, ...]
    clamped_frames: int
    clamped_dimensions: int
    transform_provenance: dict[str, object]

    @property
    def mutated_derived_data(self) -> bool:
        return self.clamped_dimensions > 0


@dataclass(frozen=True, slots=True)
class CleaningActionResult:
    """TRANSFORM + QUARANTINE 动作执行汇总。"""

    quarantined: tuple[QuarantineDecision, ...]
    transformed_slices: tuple[TransformedSlice, ...]
    raw_asset_mutated: bool = False

    @property
    def derived_data_mutated(self) -> bool:
        return any(item.mutated_derived_data for item in self.transformed_slices)

    @property
    def quarantined_target_refs(self) -> frozenset[str]:
        return frozenset(item.target_ref for item in self.quarantined)


def apply_cleaning_actions(
    slices: Sequence[CleaningStreamSlice],
    report: CleaningEvaluationReport,
    *,
    thresholds: CleaningThresholds,
    quarantine_rule_ids: AbstractSet[str] | Sequence[str] = (),
    transform_rule_ids: AbstractSet[str] | Sequence[str] = (),
) -> CleaningActionResult:
    """依据规则命中报告执行确定性 QUARANTINE 与 TRANSFORM。

    - 规则自身 ``action == QUARANTINE`` 或在 ``quarantine_rule_ids`` 中：触发隔离；
    - 规则自身 ``action == TRANSFORM`` 或在 ``transform_rule_ids`` 中：对**未隔离**
      的切片执行衍生变换（当前支持 ``CLN-NUM-003`` 步长限幅）；
    - 未支持 TRANSFORM 的规则若被显式传入 ``transform_rule_ids``，立即抛
      ``DomainValidationError``，绝不静默跳过。
    """
    explicit_quarantine = {str(item) for item in quarantine_rule_ids}
    explicit_transform = {str(item) for item in transform_rule_ids}
    unsupported = sorted(explicit_transform - SUPPORTED_TRANSFORM_RULE_IDS)
    if unsupported:
        raise DomainValidationError(f"以下清洗规则不支持 TRANSFORM 动作：{', '.join(unsupported)}")

    hits_by_target: dict[str, list[CleaningRuleHit]] = {}
    for hit in report.hits:
        hits_by_target.setdefault(hit.target_ref, []).append(hit)

    ordered = tuple(sorted(slices, key=lambda item: item.target_ref))
    quarantined = _build_quarantine_decisions(ordered, hits_by_target, explicit_quarantine)
    quarantined_refs = {item.target_ref for item in quarantined}

    transformed: list[TransformedSlice] = []
    if explicit_transform or any(
        hit.action == CleaningAction.TRANSFORM.value for hit in report.hits
    ):
        for item in ordered:
            if item.target_ref in quarantined_refs:
                continue
            slice_hits = hits_by_target.get(item.target_ref, [])
            should_clamp = RULE_ACTION_STEP_JUMP in explicit_transform or any(
                hit.rule_id == RULE_ACTION_STEP_JUMP
                and hit.action == CleaningAction.TRANSFORM.value
                for hit in slice_hits
            )
            if should_clamp:
                transformed.append(_clamp_action_slice(item, thresholds))

    return CleaningActionResult(
        quarantined=tuple(quarantined),
        transformed_slices=tuple(transformed),
        raw_asset_mutated=False,
    )


def _build_quarantine_decisions(
    ordered: Sequence[CleaningStreamSlice],
    hits_by_target: dict[str, list[CleaningRuleHit]],
    explicit_quarantine: set[str],
) -> list[QuarantineDecision]:
    decisions: list[QuarantineDecision] = []
    for item in ordered:
        matched = [
            hit
            for hit in hits_by_target.get(item.target_ref, [])
            if hit.action == CleaningAction.QUARANTINE.value or hit.rule_id in explicit_quarantine
        ]
        if not matched:
            continue
        rule_ids = tuple(sorted({hit.rule_id for hit in matched}))
        summary = "；".join(hit.evidence_summary for hit in matched[:3])
        if len(matched) > 3:
            summary = f"{summary}（共 {len(matched)} 条命中）"
        decisions.append(
            QuarantineDecision(
                target_ref=item.target_ref,
                source_ref=item.source_ref,
                rule_ids=rule_ids,
                hit_count=len(matched),
                evidence_summary=summary,
            )
        )
    return decisions


def _is_finite_vec(values: tuple[object, ...]) -> bool:
    if not values:
        return False
    return all(
        not isinstance(v, bool) and isinstance(v, int | float) and math.isfinite(float(v))
        for v in values
    )


def _clamp_action_slice(
    item: CleaningStreamSlice,
    thresholds: CleaningThresholds,
) -> TransformedSlice:
    limit = thresholds.max_action_step_delta
    new_actions: list[CleaningFrameSample] = []
    clamped_frames = 0
    clamped_dims = 0
    prev_vec: list[float] | None = None

    for sample in item.actions:
        if not _is_finite_vec(sample.values):
            new_actions.append(sample)
            prev_vec = None
            continue
        curr = [float(v) for v in sample.values]  # type: ignore[arg-type]
        if prev_vec is None or len(prev_vec) != len(curr):
            new_actions.append(sample)
            prev_vec = curr
            continue

        adjusted: list[float] = []
        frame_changed = False
        for prev_val, curr_val in zip(prev_vec, curr, strict=True):
            delta = curr_val - prev_val
            if delta > limit:
                adjusted.append(round(prev_val + limit, 9))
                clamped_dims += 1
                frame_changed = True
            elif delta < -limit:
                adjusted.append(round(prev_val - limit, 9))
                clamped_dims += 1
                frame_changed = True
            else:
                adjusted.append(curr_val)
        if frame_changed:
            clamped_frames += 1
        new_actions.append(
            CleaningFrameSample(
                record_id=sample.record_id,
                frame_index=sample.frame_index,
                values=tuple(adjusted),
                timestamp_ns=sample.timestamp_ns,
            )
        )
        prev_vec = adjusted

    return TransformedSlice(
        target_ref=item.target_ref,
        source_ref=item.source_ref,
        observations=item.observations,
        actions=tuple(new_actions),
        clamped_frames=clamped_frames,
        clamped_dimensions=clamped_dims,
        transform_provenance={
            "schema_version": TRANSFORM_SCHEMA_VERSION,
            "rule_id": RULE_ACTION_STEP_JUMP,
            "max_action_step_delta": limit,
            "threshold_profile_version": thresholds.profile_version,
            "clamped_frames": clamped_frames,
            "clamped_dimensions": clamped_dims,
        },
    )


__all__ = [
    "SUPPORTED_TRANSFORM_RULE_IDS",
    "TRANSFORM_SCHEMA_VERSION",
    "CleaningActionResult",
    "QuarantineDecision",
    "TransformedSlice",
    "apply_cleaning_actions",
]
