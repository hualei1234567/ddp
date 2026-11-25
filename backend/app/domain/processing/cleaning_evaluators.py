"""``DATA_CLEANING`` 纯函数规则谓词评估实现（V51-006）。

从 ``cleaning_rules`` 拆出谓词计算，确保每个领域文件严格低于 500 行、每个函数低于
50 行有效逻辑。本模块不触发任何 IO，对相同输入保证字节级确定性。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING

from app.domain.processing.errors import DomainValidationError
from app.domain.processing.validation import required_text

if TYPE_CHECKING:
    from app.domain.processing.cleaning_rules import (
        CleaningEvaluationReport,
        CleaningRuleHit,
        CleaningRuleSpec,
        CleaningStreamSlice,
        CleaningThresholds,
    )

RULE_NON_MONOTONIC_TIMESTAMP = "CLN-TIME-001"
RULE_NON_FINITE_OR_EMPTY_VECTOR = "CLN-NUM-001"
RULE_CONSTANT_CHANNEL = "CLN-NUM-002"
RULE_ACTION_STEP_JUMP = "CLN-NUM-003"
RULE_SHORT_EPISODE_FRAGMENT = "CLN-EPISODE-001"
RULE_OBS_ACTION_COUNT_MISMATCH = "CLN-SEM-001"


def evaluate_ruleset(
    slices: Sequence[CleaningStreamSlice],
    *,
    ruleset: Sequence[CleaningRuleSpec],
    ruleset_version: str,
    thresholds: CleaningThresholds,
    disabled_rule_ids: AbstractSet[str] | Sequence[str] = (),
) -> CleaningEvaluationReport:
    """按声明顺序对排序后的流切片执行确定性评估。"""
    from app.domain.processing.cleaning_rules import CleaningEvaluationReport

    version_text = required_text(ruleset_version, "ruleset_version", max_length=64)
    explicit_disabled = {str(item) for item in disabled_rule_ids}
    unknown = sorted(explicit_disabled - {rule.rule_id for rule in ruleset})
    if unknown:
        raise DomainValidationError(f"disabled_rule_ids 含未知规则 ID：{', '.join(unknown)}")

    ordered = tuple(sorted(slices, key=lambda item: item.target_ref))
    evaluated_ids: list[str] = []
    skipped_ids: list[str] = []
    hits: list[CleaningRuleHit] = []

    for rule in ruleset:
        if not rule.enabled or rule.rule_id in explicit_disabled:
            skipped_ids.append(rule.rule_id)
            continue
        evaluated_ids.append(rule.rule_id)
        for item in ordered:
            hits.extend(_evaluate_single_rule(rule, item, thresholds))

    return CleaningEvaluationReport(
        ruleset_version=version_text,
        thresholds=thresholds,
        evaluated_rule_ids=tuple(evaluated_ids),
        disabled_rule_ids=tuple(skipped_ids),
        evaluated_targets=tuple(item.target_ref for item in ordered),
        total_observations=sum(len(item.observations) for item in ordered),
        total_actions=sum(len(item.actions) for item in ordered),
        hits=tuple(hits),
        raw_asset_mutated=False,
        derived_data_mutated=any(hit.mutated_derived_data for hit in hits),
    )


def _evaluate_single_rule(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
    thresholds: CleaningThresholds,
) -> list[CleaningRuleHit]:
    if rule.rule_id == RULE_NON_MONOTONIC_TIMESTAMP:
        return _check_timestamps(rule, item)
    if rule.rule_id == RULE_NON_FINITE_OR_EMPTY_VECTOR:
        return _check_non_finite_or_empty(rule, item)
    if rule.rule_id == RULE_CONSTANT_CHANNEL:
        return _check_constant_channels(rule, item, thresholds)
    if rule.rule_id == RULE_ACTION_STEP_JUMP:
        return _check_action_jumps(rule, item, thresholds)
    if rule.rule_id == RULE_SHORT_EPISODE_FRAGMENT:
        return _check_short_fragment(rule, item, thresholds)
    if rule.rule_id == RULE_OBS_ACTION_COUNT_MISMATCH:
        return _check_obs_action_mismatch(rule, item)
    raise DomainValidationError(f"未注册评估实现的清洗规则：{rule.rule_id}")


def _make_hit(
    rule: CleaningRuleSpec,
    *,
    target_ref: str,
    frame_range: tuple[int, int] | None,
    field_path: str,
    observed_value: object,
    threshold: object,
    evidence_summary: str,
) -> CleaningRuleHit:
    from app.domain.processing.cleaning_rules import CleaningRuleHit

    return CleaningRuleHit(
        rule_id=rule.rule_id,
        rule_version=rule.version,
        scope=rule.scope.value,
        severity=rule.severity.value,
        action=rule.action.value,
        target_ref=target_ref,
        frame_range=frame_range,
        field_path=field_path,
        observed_value=observed_value,
        threshold=threshold,
        evidence_summary=evidence_summary,
        mutated_derived_data=False,
    )


def _check_timestamps(rule: CleaningRuleSpec, item: CleaningStreamSlice) -> list[CleaningRuleHit]:
    hits: list[CleaningRuleHit] = []
    for name, samples in (("observations", item.observations), ("actions", item.actions)):
        timed = [s for s in samples if s.timestamp_ns is not None]
        violations = [
            (prev, curr)
            for prev, curr in zip(timed, timed[1:], strict=False)
            if curr.timestamp_ns <= prev.timestamp_ns  # type: ignore[operator]
        ]
        if not violations:
            continue
        first_prev, first_curr = violations[0]
        # 纳秒整数以字符串写入 JSON 证据，防止超过 2^53 时前端精度丢失（MEM-INC-008）。
        summary = (
            f"{name} 存在 {len(violations)} 处非单调或重复时间戳"
            f"（首处于帧 {first_prev.frame_index}->{first_curr.frame_index}）"
        )
        hits.append(
            _make_hit(
                rule,
                target_ref=item.target_ref,
                frame_range=(first_prev.frame_index, violations[-1][1].frame_index),
                field_path=f"{name}.timestamp_ns",
                observed_value={
                    "violation_count": len(violations),
                    "first_prev_timestamp_ns": str(first_prev.timestamp_ns),
                    "first_curr_timestamp_ns": str(first_curr.timestamp_ns),
                },
                threshold="strictly_increasing",
                evidence_summary=summary,
            )
        )
    return hits


def _classify_bad_vector(values: tuple[object, ...]) -> tuple[int | None, str] | None:
    if not values:
        return None, "EMPTY_VECTOR"
    for dim, val in enumerate(values):
        if isinstance(val, bool) or not isinstance(val, int | float):
            return dim, "NON_NUMERIC"
        as_float = float(val)
        if math.isnan(as_float):
            return dim, "NaN"
        if math.isinf(as_float):
            return dim, "Infinity" if as_float > 0 else "-Infinity"
    return None


def _is_finite_vector(values: tuple[object, ...]) -> bool:
    return _classify_bad_vector(values) is None


def _check_non_finite_or_empty(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
) -> list[CleaningRuleHit]:
    hits: list[CleaningRuleHit] = []
    for name, samples in (("observations", item.observations), ("actions", item.actions)):
        bad = [
            (s.frame_index, *issue)
            for s in samples
            if (issue := _classify_bad_vector(s.values)) is not None
        ]
        if not bad:
            continue
        first_frame, first_dim, first_reason = bad[0]
        suffix = f"[{first_dim}]" if first_dim is not None else ""
        summary = (
            f"{name} 共 {len(bad)} 帧含空向量或非有限值" f"（首帧 {first_frame}：{first_reason}）"
        )
        hits.append(
            _make_hit(
                rule,
                target_ref=item.target_ref,
                frame_range=(first_frame, bad[-1][0]),
                field_path=f"{name}[*].values{suffix}",
                observed_value={
                    "affected_frame_count": len(bad),
                    "first_frame_index": first_frame,
                    "first_dim": first_dim,
                    "first_issue": first_reason,
                },
                threshold="non_empty_and_all_finite",
                evidence_summary=summary,
            )
        )
    return hits


def _check_constant_channels(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
    thresholds: CleaningThresholds,
) -> list[CleaningRuleHit]:
    hits: list[CleaningRuleHit] = []
    for name, samples in (("observations", item.observations), ("actions", item.actions)):
        finite = [s for s in samples if _is_finite_vector(s.values)]
        if len(finite) < thresholds.constant_channel_min_frames:
            continue
        width = len(finite[0].values)
        if width == 0 or any(len(s.values) != width for s in finite):
            continue
        constant_dims = [
            dim
            for dim in range(width)
            if max(float(s.values[dim]) for s in finite)  # type: ignore[arg-type]
            - min(float(s.values[dim]) for s in finite)  # type: ignore[arg-type]
            <= thresholds.constant_channel_epsilon
        ]
        if not constant_dims:
            continue
        summary = (
            f"{name} 有 {len(constant_dims)}/{width} 维全程方差为 0" f"（通道 {constant_dims[:8]}）"
        )
        hits.append(
            _make_hit(
                rule,
                target_ref=item.target_ref,
                frame_range=(finite[0].frame_index, finite[-1].frame_index),
                field_path=f"{name}[*].values",
                observed_value={
                    "constant_dims": constant_dims,
                    "constant_dim_count": len(constant_dims),
                    "total_dims": width,
                },
                threshold={
                    "min_frames": thresholds.constant_channel_min_frames,
                    "epsilon": thresholds.constant_channel_epsilon,
                },
                evidence_summary=summary,
            )
        )
    return hits


def _check_action_jumps(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
    thresholds: CleaningThresholds,
) -> list[CleaningRuleHit]:
    jumps: list[tuple[int, int, int, float]] = []
    for prev, curr in zip(item.actions, item.actions[1:], strict=False):
        if not (_is_finite_vector(prev.values) and _is_finite_vector(curr.values)):
            continue
        if len(prev.values) != len(curr.values) or not prev.values:
            continue
        deltas = [
            abs(float(b) - float(a))  # type: ignore[arg-type]
            for a, b in zip(prev.values, curr.values, strict=True)
        ]
        peak_dim = max(range(len(deltas)), key=deltas.__getitem__)
        if deltas[peak_dim] > thresholds.max_action_step_delta:
            jumps.append((prev.frame_index, curr.frame_index, peak_dim, deltas[peak_dim]))
    if not jumps:
        return []
    worst = max(jumps, key=lambda entry: entry[3])
    limit = thresholds.max_action_step_delta
    summary = (
        f"动作序列 {len(jumps)} 处单步突变，最大 {worst[3]:.6g}"
        f"（帧 {worst[0]}->{worst[1]}，维 {worst[2]}）超阈值 {limit}"
    )
    return [
        _make_hit(
            rule,
            target_ref=item.target_ref,
            frame_range=(jumps[0][0], jumps[-1][1]),
            field_path=f"actions[*].values[{worst[2]}]",
            observed_value={
                "jump_count": len(jumps),
                "max_abs_delta": round(worst[3], 9),
                "peak_frame_pair": [worst[0], worst[1]],
                "peak_dim": worst[2],
            },
            threshold=limit,
            evidence_summary=summary,
        )
    ]


def _check_short_fragment(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
    thresholds: CleaningThresholds,
) -> list[CleaningRuleHit]:
    primary = item.observations if item.observations else item.actions
    observed = sum(1 for s in primary if _is_finite_vector(s.values))
    limit = thresholds.min_episode_frames
    if observed >= limit:
        return []
    frame_range = (primary[0].frame_index, primary[-1].frame_index) if primary else None
    return [
        _make_hit(
            rule,
            target_ref=item.target_ref,
            frame_range=frame_range,
            field_path="episode.effective_frame_count",
            observed_value=observed,
            threshold=limit,
            evidence_summary=f"单切片有效帧数 {observed} 低于最小阈值 {limit}",
        )
    ]


def _check_obs_action_mismatch(
    rule: CleaningRuleSpec,
    item: CleaningStreamSlice,
) -> list[CleaningRuleHit]:
    obs_count, act_count = len(item.observations), len(item.actions)
    if item.action_source != "PRESENT" or obs_count == act_count:
        return []
    max_len = max(obs_count, act_count)
    summary = f"动作源 PRESENT 下观测（{obs_count} 帧）与动作（{act_count} 帧）数量不一致"
    return [
        _make_hit(
            rule,
            target_ref=item.target_ref,
            frame_range=(0, max_len - 1) if max_len > 0 else None,
            field_path="observations|actions",
            observed_value={"observation_count": obs_count, "action_count": act_count},
            threshold="len(observations) == len(actions)",
            evidence_summary=summary,
        )
    ]


__all__ = [
    "RULE_ACTION_STEP_JUMP",
    "RULE_CONSTANT_CHANNEL",
    "RULE_NON_FINITE_OR_EMPTY_VECTOR",
    "RULE_NON_MONOTONIC_TIMESTAMP",
    "RULE_OBS_ACTION_COUNT_MISMATCH",
    "RULE_SHORT_EPISODE_FRAGMENT",
    "evaluate_ruleset",
]
