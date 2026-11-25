"""``DATA_CLEANING`` 确定性清洗领域规则模型与默认规则集（V51-006）。

对齐《09_DATA_CLEANING确定性清洗设计》与《V51_Execution_DAG》T2-10：
- **严守原始资产红线**：清洗绝不修改或删除 ``RawAsset``，也不静默剔除困难样本；
  V5.1 首批规则默认采用 ``FLAG`` 风险标记与报告（``mutated_derived_data=False``）。
- **纯领域无 IO**：相同输入 + 相同规则版本 + 相同阈值配置必然得到相同输出与 SHA256。
- **可解释、可关闭、可复现**：支持按 ``disabled_rule_ids`` 关闭任意规则并在报告留痕。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import asdict, dataclass
from enum import StrEnum

from app.domain.processing.cleaning_evaluators import (
    RULE_ACTION_STEP_JUMP,
    RULE_CONSTANT_CHANNEL,
    RULE_NON_FINITE_OR_EMPTY_VECTOR,
    RULE_NON_MONOTONIC_TIMESTAMP,
    RULE_OBS_ACTION_COUNT_MISMATCH,
    RULE_SHORT_EPISODE_FRAGMENT,
    evaluate_ruleset,
)
from app.domain.processing.errors import DomainValidationError
from app.domain.processing.validation import non_negative_int, positive_int, required_text

CLEANING_RULESET_VERSION = "ddp.cleaning.ruleset/1.0.0"
DEFAULT_THRESHOLD_PROFILE_VERSION = "ddp.cleaning.thresholds.default/1.0.0"

_set = object.__setattr__


class CleaningAction(StrEnum):
    FLAG = "FLAG"
    TRANSFORM = "TRANSFORM"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"


class CleaningSeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class CleaningRuleScope(StrEnum):
    TIME = "TIME"
    NUMERIC = "NUMERIC"
    EPISODE = "EPISODE"
    SEMANTIC = "SEMANTIC"


def _finite_float(value: float, field: str, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError(f"{field} 必须是有限数值")
    coerced = float(value)
    if not math.isfinite(coerced) or (coerced <= 0.0 if positive else coerced < 0.0):
        bound = "正" if positive else "非负"
        raise DomainValidationError(f"{field} 必须是{bound}有限数值")
    return coerced


@dataclass(frozen=True, slots=True)
class CleaningThresholds:
    """可追溯的清洗阈值参数配置（不硬编码特定机器人型号物理极限）。"""

    profile_version: str = DEFAULT_THRESHOLD_PROFILE_VERSION
    min_episode_frames: int = 5
    constant_channel_min_frames: int = 5
    constant_channel_epsilon: float = 1e-12
    max_action_step_delta: float = 2.0

    def __post_init__(self) -> None:
        ver = required_text(self.profile_version, "profile_version", max_length=64)
        min_ep = positive_int(self.min_episode_frames, "min_episode_frames")
        min_const = positive_int(self.constant_channel_min_frames, "constant_channel_min_frames")
        eps = _finite_float(
            self.constant_channel_epsilon, "constant_channel_epsilon", positive=False
        )
        delta = _finite_float(self.max_action_step_delta, "max_action_step_delta", positive=True)
        _set(self, "profile_version", ver)
        _set(self, "min_episode_frames", min_ep)
        _set(self, "constant_channel_min_frames", min_const)
        _set(self, "constant_channel_epsilon", eps)
        _set(self, "max_action_step_delta", delta)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_CLEANING_THRESHOLDS = CleaningThresholds()


@dataclass(frozen=True, slots=True)
class CleaningRuleSpec:
    """单条确定性清洗规则的不可变声明（对齐蓝图 §3）。"""

    rule_id: str
    version: str
    scope: CleaningRuleScope
    input_modality: str
    predicate: str
    threshold: str
    severity: CleaningSeverity
    action: CleaningAction
    evidence_template: str
    description: str
    owner: str
    effective_date: str
    enabled: bool = True

    def __post_init__(self) -> None:
        _set(self, "rule_id", required_text(self.rule_id, "rule_id", max_length=64))
        _set(self, "version", required_text(self.version, "version", max_length=32))
        _set(self, "scope", CleaningRuleScope(self.scope))
        _set(
            self,
            "input_modality",
            required_text(self.input_modality, "input_modality", max_length=64),
        )
        _set(self, "predicate", required_text(self.predicate, "predicate", max_length=256))
        _set(self, "threshold", required_text(self.threshold, "threshold", max_length=128))
        _set(self, "severity", CleaningSeverity(self.severity))
        _set(self, "action", CleaningAction(self.action))
        _set(
            self,
            "evidence_template",
            required_text(self.evidence_template, "evidence_template", max_length=256),
        )
        _set(self, "description", required_text(self.description, "description", max_length=512))
        _set(self, "owner", required_text(self.owner, "owner", max_length=64))
        _set(
            self,
            "effective_date",
            required_text(self.effective_date, "effective_date", max_length=32),
        )
        if not isinstance(self.enabled, bool):
            raise DomainValidationError("enabled 必须是布尔值")

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "scope": self.scope.value,
            "input_modality": self.input_modality,
            "predicate": self.predicate,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "action": self.action.value,
            "evidence_template": self.evidence_template,
            "description": self.description,
            "owner": self.owner,
            "effective_date": self.effective_date,
            "enabled": self.enabled,
        }


@dataclass(frozen=True, slots=True)
class CleaningFrameSample:
    """单帧观测或动作的只读评估样本。"""

    record_id: str
    frame_index: int
    values: tuple[object, ...]
    timestamp_ns: int | None = None

    def __post_init__(self) -> None:
        _set(self, "record_id", required_text(self.record_id, "record_id", max_length=256))
        _set(self, "frame_index", non_negative_int(self.frame_index, "frame_index"))
        _set(self, "values", tuple(self.values))
        if self.timestamp_ns is not None and (
            isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int)
        ):
            raise DomainValidationError("timestamp_ns 必须是整数或 None")


@dataclass(frozen=True, slots=True)
class CleaningStreamSlice:
    """单个流提取产物（单 demo / Episode 切片）的清洗评估输入。"""

    target_ref: str
    source_ref: str | None
    action_source: str
    observations: tuple[CleaningFrameSample, ...]
    actions: tuple[CleaningFrameSample, ...]

    def __post_init__(self) -> None:
        _set(self, "target_ref", required_text(self.target_ref, "target_ref", max_length=1024))
        _set(
            self,
            "action_source",
            required_text(self.action_source, "action_source", max_length=64),
        )
        _set(self, "observations", tuple(self.observations))
        _set(self, "actions", tuple(self.actions))


@dataclass(frozen=True, slots=True)
class CleaningRuleHit:
    """单条清洗规则命中证据（不含任何不可序列化浮点 NaN/Inf）。"""

    rule_id: str
    rule_version: str
    scope: str
    severity: str
    action: str
    target_ref: str
    frame_range: tuple[int, int] | None
    field_path: str
    observed_value: object
    threshold: object
    evidence_summary: str
    mutated_derived_data: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "scope": self.scope,
            "severity": self.severity,
            "action": self.action,
            "target_ref": self.target_ref,
            "frame_range": list(self.frame_range) if self.frame_range is not None else None,
            "field_path": self.field_path,
            "observed_value": self.observed_value,
            "threshold": self.threshold,
            "evidence_summary": self.evidence_summary,
            "mutated_derived_data": self.mutated_derived_data,
        }


@dataclass(frozen=True, slots=True)
class CleaningEvaluationReport:
    """确定性清洗规则集的完整评估结论。"""

    ruleset_version: str
    thresholds: CleaningThresholds
    evaluated_rule_ids: tuple[str, ...]
    disabled_rule_ids: tuple[str, ...]
    evaluated_targets: tuple[str, ...]
    total_observations: int
    total_actions: int
    hits: tuple[CleaningRuleHit, ...]
    raw_asset_mutated: bool = False
    derived_data_mutated: bool = False

    def to_payload(self) -> dict[str, object]:
        by_severity = Counter(hit.severity for hit in self.hits)
        by_action = Counter(hit.action for hit in self.hits)
        return {
            "ruleset_version": self.ruleset_version,
            "threshold_profile_version": self.thresholds.profile_version,
            "thresholds": self.thresholds.to_dict(),
            "evaluated_rule_ids": list(self.evaluated_rule_ids),
            "disabled_rule_ids": list(self.disabled_rule_ids),
            "evaluated_targets": list(self.evaluated_targets),
            "raw_asset_mutated": self.raw_asset_mutated,
            "derived_data_mutated": self.derived_data_mutated,
            "summary": {
                "target_count": len(self.evaluated_targets),
                "total_observations": self.total_observations,
                "total_actions": self.total_actions,
                "hit_count": len(self.hits),
                "severity_counts": dict(sorted(by_severity.items())),
                "action_counts": dict(sorted(by_action.items())),
            },
            "hits": [hit.to_dict() for hit in self.hits],
        }

    def deterministic_sha256(self) -> str:
        canonical = json.dumps(
            self.to_payload(), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


DEFAULT_CLEANING_RULESET: tuple[CleaningRuleSpec, ...] = (
    CleaningRuleSpec(
        rule_id=RULE_NON_MONOTONIC_TIMESTAMP,
        version="1.0.0",
        scope=CleaningRuleScope.TIME,
        input_modality="STATE|ACTION",
        predicate="timestamp_ns[i] > timestamp_ns[i - 1]",
        threshold="strictly_increasing(timestamp_ns)",
        severity=CleaningSeverity.WARN,
        action=CleaningAction.FLAG,
        evidence_template="{stream_kind} 存在 {count} 处时间戳回退或重复",
        description="检测观测或动作序列中的非单调递增或重复时间戳",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
    CleaningRuleSpec(
        rule_id=RULE_NON_FINITE_OR_EMPTY_VECTOR,
        version="1.0.0",
        scope=CleaningRuleScope.NUMERIC,
        input_modality="STATE|ACTION",
        predicate="len(values) > 0 and all(isfinite(v) for v in values)",
        threshold="non_empty_and_all_finite",
        severity=CleaningSeverity.ERROR,
        action=CleaningAction.FLAG,
        evidence_template="{stream_kind} 存在 {count} 帧空向量或非有限值",
        description="检测观测或动作向量中的空向量、NaN、Inf 或非法数值元素",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
    CleaningRuleSpec(
        rule_id=RULE_CONSTANT_CHANNEL,
        version="1.0.0",
        scope=CleaningRuleScope.NUMERIC,
        input_modality="STATE|ACTION",
        predicate="max(channel) - min(channel) > constant_channel_epsilon",
        threshold="constant_channel_min_frames & constant_channel_epsilon",
        severity=CleaningSeverity.WARN,
        action=CleaningAction.FLAG,
        evidence_template="{stream_kind} 有 {count}/{width} 维全程零方差",
        description="帧数达到最小评估窗口时，检测全程无变化的零方差常量通道",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
    CleaningRuleSpec(
        rule_id=RULE_ACTION_STEP_JUMP,
        version="1.0.0",
        scope=CleaningRuleScope.NUMERIC,
        input_modality="ACTION",
        predicate="max_d |action[t][d] - action[t - 1][d]| <= max_action_step_delta",
        threshold="max_action_step_delta",
        severity=CleaningSeverity.WARN,
        action=CleaningAction.FLAG,
        evidence_template="动作相邻帧最大突变 {peak_delta} 超过阈值 {limit}",
        description="检测相邻帧动作向量单步幅值突变异常",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
    CleaningRuleSpec(
        rule_id=RULE_SHORT_EPISODE_FRAGMENT,
        version="1.0.0",
        scope=CleaningRuleScope.EPISODE,
        input_modality="EPISODE",
        predicate="effective_frame_count >= min_episode_frames",
        threshold="min_episode_frames",
        severity=CleaningSeverity.WARN,
        action=CleaningAction.FLAG,
        evidence_template="单切片有效帧数 {observed} 低于最小阈值 {limit}",
        description="检测有效帧数过短的碎片化 Episode/Demo 切片",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
    CleaningRuleSpec(
        rule_id=RULE_OBS_ACTION_COUNT_MISMATCH,
        version="1.0.0",
        scope=CleaningRuleScope.SEMANTIC,
        input_modality="STATE|ACTION",
        predicate="action_source != 'PRESENT' or len(observations) == len(actions)",
        threshold="len(observations) == len(actions)",
        severity=CleaningSeverity.WARN,
        action=CleaningAction.FLAG,
        evidence_template="动作源存在但观测帧数与动作帧数不一致",
        description="当声明存在动作源时，校验同一切片内观测与动作帧数严格对齐",
        owner="data-platform",
        effective_date="2026-11-25",
    ),
)


def evaluate_stream_records(
    slices: Sequence[CleaningStreamSlice],
    *,
    ruleset: Sequence[CleaningRuleSpec] = DEFAULT_CLEANING_RULESET,
    ruleset_version: str = CLEANING_RULESET_VERSION,
    thresholds: CleaningThresholds = DEFAULT_CLEANING_THRESHOLDS,
    disabled_rule_ids: AbstractSet[str] | Sequence[str] = (),
) -> CleaningEvaluationReport:
    """纯函数评估流切片集合：相同输入 + 相同规则集 + 相同阈值 → 100% 相同输出。"""
    return evaluate_ruleset(
        slices,
        ruleset=ruleset,
        ruleset_version=ruleset_version,
        thresholds=thresholds,
        disabled_rule_ids=disabled_rule_ids,
    )


__all__ = [
    "CLEANING_RULESET_VERSION",
    "DEFAULT_CLEANING_RULESET",
    "DEFAULT_CLEANING_THRESHOLDS",
    "DEFAULT_THRESHOLD_PROFILE_VERSION",
    "RULE_ACTION_STEP_JUMP",
    "RULE_CONSTANT_CHANNEL",
    "RULE_NON_FINITE_OR_EMPTY_VECTOR",
    "RULE_NON_MONOTONIC_TIMESTAMP",
    "RULE_OBS_ACTION_COUNT_MISMATCH",
    "RULE_SHORT_EPISODE_FRAGMENT",
    "CleaningAction",
    "CleaningEvaluationReport",
    "CleaningFrameSample",
    "CleaningRuleHit",
    "CleaningRuleScope",
    "CleaningRuleSpec",
    "CleaningSeverity",
    "CleaningStreamSlice",
    "CleaningThresholds",
    "evaluate_stream_records",
]
