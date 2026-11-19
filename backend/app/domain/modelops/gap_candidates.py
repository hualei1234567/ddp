"""评测失败事实 → 候选缺口（N7-005）：只读、可解释、绝不自动转需求。

定位
----
N7-004 的 ``gap_evidence`` 回答"某个既有缺口结论有哪些来源支撑"；本模块回答
"当前项目的评测失败事实本身指向哪些候选缺口，以及哪些候选无法归因"。候选
**不是**缺口、**不是**需求、**不是**权威维度：

- 只有 ``suggested_gap`` 与既有维度键（反馈或实测建立的键）唯一匹配时，候选
  才带 ``dimension_type`` / ``dimension_value``，并允许人工转需求；
- 匹配不上或同名值命中多个轴时进入 ``unmapped``，显式给出原因，不猜测；
- 没有提示值的失败用例按 ``category`` 聚合成候选，同样进入 ``unmapped``——
  能力环节不是 Data Gap 维度，不能冒充场景/动作。

硬约束
------
1. **提示值不是权威维度**：候选只复用既有维度键，绝不创建新键；
2. **不自动转需求/审批**：本模块只产出只读视图，创建需求必须由显式人工动作
   调用既有需求用例（见 ``app.application.modelops.gap_candidates``）；
3. **有界**：评测扫描上限由基础设施声明；``limit`` 限制两类候选的返回条数，
   被截断时如实标记，不静默丢数据；
4. **确定性**：同一批事实与既有键得到同一结果；不读时钟、不访问数据库。

匹配规则直接复用 :func:`app.domain.modelops.gap_evidence.match_dimension_hint`，
全项目只有一套"trim + casefold + 唯一命中"口径。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from app.domain.modelops.enums import (
    FailureCaseCategory,
    FeedbackSeverity,
    GapDimensionType,
)
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.failure_cases import FailureCase
from app.domain.modelops.gap_evidence import (
    GapUnmappedReason,
    match_dimension_hint,
    normalize_dimension_hint,
)
from app.domain.modelops.validation import (
    coerce_enum,
    non_negative_int,
    positive_int,
    required_uuid,
    text_value,
)

# 候选列表的返回上限（不是业务阈值，是响应边界）。转需求一次只处理一个候选，
# 不提供批量自动转换入口。
MAX_CANDIDATE_LIMIT = 200
# 单个候选最多回带多少条来源评测 ID：够追溯即可，避免响应随失败历史无限膨胀。
MAX_CANDIDATE_SOURCE_EVALUATIONS = 20
MAX_CANDIDATE_LABEL_LENGTH = 200

_CATEGORY_ORDER = {item.value: index for index, item in enumerate(FailureCaseCategory)}
_SEVERITY_ORDER = {item.value: index for index, item in enumerate(FeedbackSeverity)}


class GapCandidateKind(StrEnum):
    """候选来源形态。

    - ``SUGGESTED_GAP``：适配上送的 ``suggested_gap`` 提示值聚合；
    - ``CATEGORY``：没有提示值的失败用例按能力环节聚合，仅作事实汇总。
    """

    SUGGESTED_GAP = "SUGGESTED_GAP"
    CATEGORY = "CATEGORY"


@dataclass(frozen=True, slots=True)
class FailureCaseFact:
    """一条失败事实及其所属评测。

    领域层不关心数据库行结构；基础设施负责把 ``failure_cases`` 列解析成
    :class:`FailureCase` 后连同评测 ID 一起返回。
    """

    evaluation_id: UUID
    case: FailureCase

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evaluation_id", required_uuid(self.evaluation_id, "evaluation_id")
        )
        if not isinstance(self.case, FailureCase):
            raise DomainValidationError("case 必须是 FailureCase")


@dataclass(frozen=True, slots=True)
class FailureCaseScan:
    """一次有界失败事实扫描的结果。

    ``truncated`` 为真时"没有出现某个候选"不能读成"该候选不存在"；
    ``invalid_evaluation_count`` 是存量行解析失败的评测条数，显式计数而非丢弃。
    """

    facts: tuple[FailureCaseFact, ...] = ()
    evaluations_scanned: int = 0
    invalid_evaluation_count: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        for fact in self.facts:
            if not isinstance(fact, FailureCaseFact):
                raise DomainValidationError("facts 必须是 FailureCaseFact")
        object.__setattr__(
            self,
            "evaluations_scanned",
            non_negative_int(self.evaluations_scanned, "evaluations_scanned"),
        )
        object.__setattr__(
            self,
            "invalid_evaluation_count",
            non_negative_int(self.invalid_evaluation_count, "invalid_evaluation_count"),
        )
        if not isinstance(self.truncated, bool):
            raise DomainValidationError("truncated 必须是布尔值")


def candidate_key_for(kind: str, value: str) -> str:
    """稳定、URL 安全的候选标识。

    ``SUGGESTED_GAP`` 用归一化取值的摘要（大小写/空白变体得到同一个键）；
    ``CATEGORY`` 用类别枚举值。键只用于定位，**不代表**候选已被确认。
    """
    normalized_kind = coerce_enum(kind, GapCandidateKind, "kind")
    if normalized_kind is GapCandidateKind.CATEGORY:
        category = coerce_enum(value, FailureCaseCategory, "category")
        return f"cat_{category.value}"
    normalized = normalize_dimension_hint(value)
    if not normalized:
        raise DomainValidationError("suggested_gap 不能为空")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"sg_{digest}"


def _ordered_values(
    values: Iterable[str], order: dict[str, int], field_name: str
) -> tuple[str, ...]:
    unique = {str(value) for value in values}
    if not unique:
        raise DomainValidationError(f"{field_name} 不能为空")
    if any(value not in order for value in unique):
        raise DomainValidationError(f"{field_name} 含未知取值")
    return tuple(sorted(unique, key=order.__getitem__))


@dataclass(frozen=True, slots=True)
class GapCandidate:
    """可人工转需求的候选：``dimension_*`` 一定来自既有维度键。"""

    candidate_key: str
    kind: str
    label: str
    failure_count: int
    evaluation_count: int
    categories: tuple[str, ...]
    severities: tuple[str, ...]
    dimension_type: str
    dimension_value: str
    source_evaluation_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_key", text_value(self.candidate_key, "candidate_key", max_length=64)
        )
        object.__setattr__(self, "kind", coerce_enum(self.kind, GapCandidateKind, "kind").value)
        object.__setattr__(
            self, "label", text_value(self.label, "label", max_length=MAX_CANDIDATE_LABEL_LENGTH)
        )
        object.__setattr__(self, "failure_count", positive_int(self.failure_count, "failure_count"))
        object.__setattr__(
            self, "evaluation_count", positive_int(self.evaluation_count, "evaluation_count")
        )
        object.__setattr__(
            self, "categories", _ordered_values(self.categories, _CATEGORY_ORDER, "categories")
        )
        object.__setattr__(
            self, "severities", _ordered_values(self.severities, _SEVERITY_ORDER, "severities")
        )
        object.__setattr__(
            self,
            "dimension_type",
            coerce_enum(self.dimension_type, GapDimensionType, "dimension_type").value,
        )
        object.__setattr__(
            self,
            "dimension_value",
            text_value(self.dimension_value, "dimension_value", max_length=200),
        )
        object.__setattr__(
            self,
            "source_evaluation_ids",
            _bounded_uuids(self.source_evaluation_ids),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "candidate_key": self.candidate_key,
            "kind": self.kind,
            "label": self.label,
            "failure_count": self.failure_count,
            "evaluation_count": self.evaluation_count,
            "categories": list(self.categories),
            "severities": list(self.severities),
            "dimension_type": self.dimension_type,
            "dimension_value": self.dimension_value,
            "source_evaluation_ids": [str(item) for item in self.source_evaluation_ids],
        }


@dataclass(frozen=True, slots=True)
class GapUnmappedCandidate:
    """无法归因到既有维度的候选；仍然如实列出，但不允许自动转需求。"""

    candidate_key: str
    kind: str
    label: str
    failure_count: int
    evaluation_count: int
    categories: tuple[str, ...]
    severities: tuple[str, ...]
    unmapped_reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_key", text_value(self.candidate_key, "candidate_key", max_length=64)
        )
        object.__setattr__(self, "kind", coerce_enum(self.kind, GapCandidateKind, "kind").value)
        object.__setattr__(
            self, "label", text_value(self.label, "label", max_length=MAX_CANDIDATE_LABEL_LENGTH)
        )
        object.__setattr__(self, "failure_count", positive_int(self.failure_count, "failure_count"))
        object.__setattr__(
            self, "evaluation_count", positive_int(self.evaluation_count, "evaluation_count")
        )
        object.__setattr__(
            self, "categories", _ordered_values(self.categories, _CATEGORY_ORDER, "categories")
        )
        object.__setattr__(
            self, "severities", _ordered_values(self.severities, _SEVERITY_ORDER, "severities")
        )
        object.__setattr__(
            self,
            "unmapped_reason",
            coerce_enum(self.unmapped_reason, GapUnmappedReason, "unmapped_reason").value,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "candidate_key": self.candidate_key,
            "kind": self.kind,
            "label": self.label,
            "failure_count": self.failure_count,
            "evaluation_count": self.evaluation_count,
            "categories": list(self.categories),
            "severities": list(self.severities),
            "unmapped_reason": self.unmapped_reason,
        }


@dataclass(frozen=True, slots=True)
class GapCandidateSummary:
    """候选缺口只读视图。

    ``items`` 是已归因、可人工转需求的候选；``unmapped`` 是无法归因的候选，
    两者都受 ``limit`` 约束并各自有截断标记。``scan_truncated`` 表示评测扫描
    触达服务端上限——此时"没有某个候选"不能读成"该候选不存在"。
    """

    items: tuple[GapCandidate, ...] = ()
    unmapped: tuple[GapUnmappedCandidate, ...] = ()
    items_truncated: bool = False
    unmapped_truncated: bool = False
    scan_truncated: bool = False
    scanned_evaluation_count: int = 0
    invalid_evaluation_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "unmapped", tuple(self.unmapped))
        for name in ("items_truncated", "unmapped_truncated", "scan_truncated"):
            if not isinstance(getattr(self, name), bool):
                raise DomainValidationError(f"{name} 必须是布尔值")
        for name in ("scanned_evaluation_count", "invalid_evaluation_count"):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))

    def to_payload(self) -> dict[str, object]:
        return {
            "items": [item.to_payload() for item in self.items],
            "unmapped": [item.to_payload() for item in self.unmapped],
            "items_truncated": self.items_truncated,
            "unmapped_truncated": self.unmapped_truncated,
            "scan_truncated": self.scan_truncated,
            "scanned_evaluation_count": self.scanned_evaluation_count,
            "invalid_evaluation_count": self.invalid_evaluation_count,
        }


def _bounded_uuids(values: Iterable[UUID]) -> tuple[UUID, ...]:
    unique: dict[UUID, None] = {}
    for value in values:
        unique.setdefault(required_uuid(value, "source_evaluation_id"), None)
        if len(unique) > MAX_CANDIDATE_SOURCE_EVALUATIONS:
            raise DomainValidationError(
                f"source_evaluation_ids 最多 {MAX_CANDIDATE_SOURCE_EVALUATIONS} 项"
            )
    return tuple(unique)


@dataclass(slots=True)
class _Bucket:
    """聚合中间态；只在构建期间存在，不进响应。"""

    kind: str
    label_counts: dict[str, int] = field(default_factory=dict)
    failure_count: int = 0
    evaluation_ids: dict[UUID, None] = field(default_factory=dict)
    categories: set[str] = field(default_factory=set)
    severities: set[str] = field(default_factory=set)

    def display_label(self) -> str:
        # 同一归一化取值的不同写法：取出现次数最多的原样文本，平票取字面序最小的，
        # 保证输出与输入顺序无关。
        return min(self.label_counts.items(), key=lambda item: (-item[1], item[0]))[0]

    def source_evaluation_ids(self) -> tuple[UUID, ...]:
        return tuple(list(self.evaluation_ids)[:MAX_CANDIDATE_SOURCE_EVALUATIONS])


def build_gap_candidates(
    facts: Iterable[FailureCaseFact],
    *,
    known_keys: Iterable[tuple[str, str]],
    limit: int = 50,
    scan_truncated: bool = False,
    evaluations_scanned: int = 0,
    invalid_evaluation_count: int = 0,
) -> GapCandidateSummary:
    """把失败事实聚合成候选缺口视图。

    ``known_keys`` 是缺口视图当前已存在的 ``(dimension_type, dimension_value)``；
    候选只挂到这些键上。``limit`` 超过 :data:`MAX_CANDIDATE_LIMIT` 时直接拒绝，
    避免调用方无限放大响应。
    """
    limit = positive_int(limit, "limit")
    if limit > MAX_CANDIDATE_LIMIT:
        raise DomainValidationError(f"limit 不能超过 {MAX_CANDIDATE_LIMIT}")
    keys = tuple((str(item[0]), str(item[1])) for item in known_keys)

    buckets: dict[tuple[str, str], _Bucket] = {}
    for fact in facts:
        if not isinstance(fact, FailureCaseFact):
            raise DomainValidationError("facts 必须是 FailureCaseFact")
        case = fact.case
        if case.suggested_gap is None:
            identity = (GapCandidateKind.CATEGORY.value, case.category)
            label = case.category
        else:
            identity = (
                GapCandidateKind.SUGGESTED_GAP.value,
                normalize_dimension_hint(case.suggested_gap),
            )
            label = case.suggested_gap.strip()
        bucket = buckets.get(identity)
        if bucket is None:
            bucket = _Bucket(kind=identity[0])
            buckets[identity] = bucket
        bucket.failure_count += 1
        bucket.label_counts[label] = bucket.label_counts.get(label, 0) + 1
        bucket.evaluation_ids.setdefault(fact.evaluation_id, None)
        bucket.categories.add(case.category)
        bucket.severities.add(case.severity)

    matched: list[GapCandidate] = []
    unmapped: list[GapUnmappedCandidate] = []
    for (kind, identity_value), bucket in buckets.items():
        label = bucket.display_label()
        candidate_key = candidate_key_for(kind, identity_value)
        if kind == GapCandidateKind.SUGGESTED_GAP.value:
            dimension_key, reason = match_dimension_hint(label, keys)
        else:
            dimension_key, reason = None, GapUnmappedReason.NO_DIMENSION
        common = {
            "candidate_key": candidate_key,
            "kind": kind,
            "label": label,
            "failure_count": bucket.failure_count,
            "evaluation_count": len(bucket.evaluation_ids),
            "categories": tuple(bucket.categories),
            "severities": tuple(bucket.severities),
        }
        if dimension_key is None:
            unmapped.append(
                GapUnmappedCandidate(
                    **common,
                    unmapped_reason=(reason or GapUnmappedReason.NO_DIMENSION).value,
                )
            )
            continue
        matched.append(
            GapCandidate(
                **common,
                dimension_type=dimension_key[0],
                dimension_value=dimension_key[1],
                source_evaluation_ids=bucket.source_evaluation_ids(),
            )
        )

    matched.sort(
        key=lambda item: (
            -item.failure_count,
            item.dimension_type,
            item.dimension_value,
            item.candidate_key,
        )
    )
    unmapped.sort(key=lambda item: (-item.failure_count, item.kind, item.label, item.candidate_key))
    return GapCandidateSummary(
        items=tuple(matched[:limit]),
        unmapped=tuple(unmapped[:limit]),
        items_truncated=len(matched) > limit,
        unmapped_truncated=len(unmapped) > limit,
        scan_truncated=bool(scan_truncated),
        scanned_evaluation_count=evaluations_scanned,
        invalid_evaluation_count=invalid_evaluation_count,
    )


__all__ = [
    "FailureCaseFact",
    "FailureCaseScan",
    "GapCandidate",
    "GapCandidateKind",
    "GapCandidateSummary",
    "GapUnmappedCandidate",
    "MAX_CANDIDATE_LIMIT",
    "MAX_CANDIDATE_SOURCE_EVALUATIONS",
    "build_gap_candidates",
    "candidate_key_for",
]
