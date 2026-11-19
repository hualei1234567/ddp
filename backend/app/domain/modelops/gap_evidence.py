"""数据缺口的多来源证据（N7-004）。

为什么单独成模块
----------------
``gaps.py`` 回答的是"项目在某个维度上缺多少数据"（``current_count`` 与
``suggested_episode_count``）；本模块回答的是另一个问题：**这个缺口结论有哪些
真实来源在支撑**。两件事混在一起会让聚合函数既算数值又做归因，规则更难审查。

来源与归因规则
--------------
- ``FEEDBACK``：人工反馈的维度本身就是缺口键；
- ``REQUIREMENT``：未满足的数据需求（"还缺哪些技能/场景"）自带维度；
- ``FAILURE_CASE``：评测失败用例的 ``suggested_gap`` 是提示值，不是权威维度。
  它按**归一化后的取值**与既有缺口键匹配（去空白、忽略大小写），匹配不到或
  同时命中多个维度类型时一律进"未映射"，不猜；
- ``LOW_SCORE``：整场评测低分没有维度语义，只能作为项目级证据进入"未映射"；
- ``ANNOTATION_CONFLICT``：``ESCALATED`` 提交里的 ``labels[].label_key`` 是
  真实的标签轴，归一到 ``LABEL``；取不出标签键时进"未映射"。

硬约束
------
1. **证据不造维度**：来源只能挂到已经由反馈或实测覆盖建立起来的维度键上；
   挂不上的条目进入 :class:`GapUnmappedEvidence`，既不丢弃也不硬塞；
2. **证据不改覆盖口径**：``current_count`` 仍只来自实测或人工值，"没有证据"
   绝不等于"覆盖为 0"；
3. **不改既有状态/阈值**：本模块不引入新的业务阈值；扫描上限属于资源边界，
   由基础设施实现声明并通过 ``truncated_sources`` 如实暴露。

本模块是纯函数，不访问数据库、不读时钟。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.domain.modelops.enums import GapDimensionType, RequirementStatus
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.validation import coerce_enum, non_negative_int, positive_int, text_value

if TYPE_CHECKING:
    # 只在类型检查时需要：``models`` → ``gaps`` → 本模块，运行时导入会成环。
    from app.domain.modelops.models import DataRequirement

_MAX_DIMENSION_VALUE = 200


class GapEvidenceSource(StrEnum):
    FEEDBACK = "FEEDBACK"
    REQUIREMENT = "REQUIREMENT"
    FAILURE_CASE = "FAILURE_CASE"
    LOW_SCORE = "LOW_SCORE"
    ANNOTATION_CONFLICT = "ANNOTATION_CONFLICT"


class GapUnmappedReason(StrEnum):
    """条目无法归因到既有维度键时的原因；机器可读，不是中文文案。"""

    # 来源本身没有维度语义（整场评测低分、取不出标签键的分歧提交）。
    NO_DIMENSION = "NO_DIMENSION"
    # 携带了维度提示值，但在既有缺口键里找不到对应值。
    VALUE_NOT_FOUND = "VALUE_NOT_FOUND"
    # 同名值同时命中多个维度类型；不替用户做选择。
    AMBIGUOUS_VALUE = "AMBIGUOUS_VALUE"
    # 存量行无法按领域规则解析（写入时已校验，理论上不应出现）；显式计数而不是静默丢弃。
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


# 缺口视角下"仍未满足"的需求状态：DRAFT 尚未提交，但仍是已登记的数据诉求；
# SATISFIED / CLOSED 表示数据已交付，REJECTED / CANCELLED 表示诉求已撤销，
# 四者都不再是缺口。
OPEN_REQUIREMENT_STATUSES: frozenset[str] = frozenset(
    {
        RequirementStatus.DRAFT.value,
        RequirementStatus.SUBMITTED.value,
        RequirementStatus.APPROVED.value,
        RequirementStatus.PLANNED.value,
        RequirementStatus.IN_PROGRESS.value,
    }
)

_SOURCE_ORDER = {item: index for index, item in enumerate(GapEvidenceSource)}


@dataclass(frozen=True, slots=True)
class GapEvidenceRecord:
    """一条待归因的证据事实。

    - ``dimension_type`` + ``dimension_value``：来源自带的权威维度键；
    - 只有 ``dimension_value``：只是提示值（见 ``FAILURE_CASE``），需与既有键匹配；
    - 两者都为空：来源没有维度语义，``reason`` 必须说明为什么无法归因。
    """

    source: str
    count: int
    dimension_type: str | None = None
    dimension_value: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            coerce_enum(self.source, GapEvidenceSource, "evidence.source").value,
        )
        object.__setattr__(self, "count", positive_int(self.count, "evidence.count"))
        if self.dimension_type is not None:
            object.__setattr__(
                self,
                "dimension_type",
                coerce_enum(self.dimension_type, GapDimensionType, "evidence.dimension_type").value,
            )
            if self.dimension_value is None:
                raise DomainValidationError("evidence 指定维度类型时必须给出维度值")
            object.__setattr__(
                self,
                "dimension_value",
                text_value(
                    self.dimension_value,
                    "evidence.dimension_value",
                    max_length=_MAX_DIMENSION_VALUE,
                ),
            )
        elif self.dimension_value is not None:
            object.__setattr__(
                self,
                "dimension_value",
                text_value(
                    self.dimension_value,
                    "evidence.dimension_value",
                    max_length=_MAX_DIMENSION_VALUE,
                ),
            )
            if self.reason is not None:
                raise DomainValidationError("带维度提示值的证据不能预设未映射原因")
        else:
            if self.reason is None:
                raise DomainValidationError("无维度证据必须给出未映射原因")
            object.__setattr__(
                self, "reason", coerce_enum(self.reason, GapUnmappedReason, "evidence.reason").value
            )

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "count": self.count,
            "dimension_type": self.dimension_type,
            "dimension_value": self.dimension_value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class GapSourceCount:
    source: str
    count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", coerce_enum(self.source, GapEvidenceSource, "source").value
        )
        object.__setattr__(self, "count", non_negative_int(self.count, "source.count"))

    def to_payload(self) -> dict[str, object]:
        return {"source": self.source, "count": self.count}


@dataclass(frozen=True, slots=True)
class GapUnmappedEvidence:
    source: str
    count: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", coerce_enum(self.source, GapEvidenceSource, "source").value
        )
        object.__setattr__(self, "count", non_negative_int(self.count, "unmapped.count"))
        object.__setattr__(
            self, "reason", coerce_enum(self.reason, GapUnmappedReason, "unmapped.reason").value
        )

    def to_payload(self) -> dict[str, object]:
        return {"source": self.source, "count": self.count, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class GapEvidenceFacts:
    """基础设施返回的原始证据；归因规则不在基础设施里执行。"""

    records: tuple[GapEvidenceRecord, ...] = ()
    truncated_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "truncated_sources",
            tuple(
                coerce_enum(item, GapEvidenceSource, "truncated_sources").value
                for item in self.truncated_sources
            ),
        )


@dataclass(frozen=True, slots=True)
class GapDimensionEvidence:
    dimension_type: str
    dimension_value: str
    sources: tuple[GapSourceCount, ...]


@dataclass(frozen=True, slots=True)
class GapEvidenceAttribution:
    entries: tuple[GapDimensionEvidence, ...] = ()
    unmapped: tuple[GapUnmappedEvidence, ...] = ()

    def sources_for(self, dimension_type: str, dimension_value: str) -> tuple[GapSourceCount, ...]:
        for entry in self.entries:
            if entry.dimension_type == dimension_type and entry.dimension_value == dimension_value:
                return entry.sources
        return ()


def normalize_dimension_hint(value: str) -> str:
    """提示值归一口径：去首尾空白、忽略大小写。

    N7-004 的证据归因与 N7-005 的候选缺口聚合共用这一实现；新增调用方
    不得再写第二套"trim + casefold"。
    """
    return value.strip().casefold()


def _known_key_index(
    known_keys: Iterable[tuple[str, str]],
) -> tuple[frozenset[tuple[str, str]], dict[str, list[tuple[str, str]]]]:
    keys = frozenset((str(item[0]), str(item[1])) for item in known_keys)
    by_value: dict[str, list[tuple[str, str]]] = {}
    for key in keys:
        by_value.setdefault(normalize_dimension_hint(key[1]), []).append(key)
    return keys, by_value


def _match_from_index(
    normalized: str,
    by_value: Mapping[str, list[tuple[str, str]]],
) -> tuple[tuple[str, str] | None, GapUnmappedReason | None]:
    matches = by_value.get(normalized, [])
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, GapUnmappedReason.VALUE_NOT_FOUND
    return None, GapUnmappedReason.AMBIGUOUS_VALUE


def match_dimension_hint(
    value: str,
    known_keys: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str] | None, GapUnmappedReason | None]:
    """把提示值匹配到**既有**维度键，绝不创建新维度。

    返回 ``(key, None)`` 表示唯一命中；``(None, reason)`` 表示无法归因：
    找不到对应值为 ``VALUE_NOT_FOUND``，同名值命中多个维度类型为
    ``AMBIGUOUS_VALUE``（不替用户做选择）。
    """
    _, by_value = _known_key_index(known_keys)
    return _match_from_index(normalize_dimension_hint(value), by_value)


def attribute_gap_evidence(
    records: Iterable[GapEvidenceRecord],
    known_keys: Iterable[tuple[str, str]],
) -> GapEvidenceAttribution:
    """把证据归因到既有维度键，返回每键来源计数与未映射汇总。

    ``known_keys`` 是缺口视图当前已经存在的 ``(dimension_type, dimension_value)``；
    证据不会创建新键。匹配按去除首尾空白、忽略大小写后的取值进行。
    """
    keys, by_value = _known_key_index(known_keys)
    attributed: dict[tuple[str, str], dict[str, int]] = {}
    unmapped: dict[tuple[str, str], int] = {}

    def _add_source(key: tuple[str, str], source: str, count: int) -> None:
        bucket = attributed.setdefault(key, {})
        bucket[source] = bucket.get(source, 0) + count

    def _add_unmapped(source: str, reason: str, count: int) -> None:
        unmapped[(source, reason)] = unmapped.get((source, reason), 0) + count

    for record in records:
        if record.dimension_type is not None:
            key = (record.dimension_type, record.dimension_value or "")
            if key in keys:
                _add_source(key, record.source, record.count)
            else:
                _add_unmapped(record.source, GapUnmappedReason.VALUE_NOT_FOUND.value, record.count)
        elif record.dimension_value is not None:
            key, reason = _match_from_index(
                normalize_dimension_hint(record.dimension_value), by_value
            )
            if key is not None:
                _add_source(key, record.source, record.count)
            else:
                _add_unmapped(
                    record.source,
                    (reason or GapUnmappedReason.VALUE_NOT_FOUND).value,
                    record.count,
                )
        else:
            reason = record.reason or GapUnmappedReason.NO_DIMENSION.value
            _add_unmapped(record.source, reason, record.count)

    entries = tuple(
        GapDimensionEvidence(
            dimension_type=key[0],
            dimension_value=key[1],
            sources=tuple(
                GapSourceCount(source=source, count=count)
                for source, count in sorted(
                    bucket.items(), key=lambda item: _SOURCE_ORDER[GapEvidenceSource(item[0])]
                )
                if count > 0
            ),
        )
        for key, bucket in sorted(attributed.items())
    )
    return GapEvidenceAttribution(
        entries=entries,
        unmapped=tuple(
            GapUnmappedEvidence(source=source, count=count, reason=reason)
            for (source, reason), count in sorted(unmapped.items())
            if count > 0
        ),
    )


def open_requirement_evidence(
    requirements: Sequence[DataRequirement],
    *,
    dimension_type: str | None = None,
) -> list[GapEvidenceRecord]:
    """未满足的数据需求按自身维度计一条来源证据。

    ``dimension_type`` 非空时只保留该轴：调用方在做单轴视图，其他轴的证据
    会在归因阶段变成"未映射"，那会把无关轴的事实混进当前视图。
    """
    records: list[GapEvidenceRecord] = []
    for requirement in requirements:
        if requirement.status not in OPEN_REQUIREMENT_STATUSES:
            continue
        for dimension in requirement.dimensions:
            if dimension_type is not None and dimension.dimension_type != dimension_type:
                continue
            records.append(
                GapEvidenceRecord(
                    source=GapEvidenceSource.REQUIREMENT.value,
                    count=1,
                    dimension_type=dimension.dimension_type,
                    dimension_value=dimension.dimension_value,
                )
            )
    return records


def feedback_evidence(
    feedback_dimensions: Iterable[tuple[str, str]],
    *,
    dimension_type: str | None = None,
) -> list[GapEvidenceRecord]:
    """人工反馈条目按维度计一条来源证据；与 ``open_feedback_count`` 同口径。

    入参是 ``(dimension_type, dimension_value)`` 序列：领域模块之间不通过
    ``gaps.GapDimension`` 互导，避免 ``gaps`` → ``gap_evidence`` 的循环依赖。
    """
    records: list[GapEvidenceRecord] = []
    for current_type, current_value in feedback_dimensions:
        if dimension_type is not None and current_type != dimension_type:
            continue
        records.append(
            GapEvidenceRecord(
                source=GapEvidenceSource.FEEDBACK.value,
                count=1,
                dimension_type=current_type,
                dimension_value=current_value,
            )
        )
    return records


def filter_evidence_axis(
    records: Iterable[GapEvidenceRecord],
    *,
    dimension_type: str | None,
    all_known_keys: Iterable[tuple[str, str]] = (),
) -> list[GapEvidenceRecord]:
    """单轴视图只保留该轴证据，以及尚待匹配的提示值。

    - 已带维度类型且不是目标轴 → 丢弃（它属于别的轴）；
    - 无维度类型且无维度值（整场低分等）→ 丢弃（它是项目级事实，只属于全量视图）；
    - 只有提示值 → 保留，交给归因阶段与目标轴的既有键匹配；若该提示值明确命中
      **别的轴**的既有键（``all_known_keys`` 里存在同名值且都不是目标轴），也丢弃，
      避免把动作提示值报成场景视图的"未映射"。命中不了任何轴的提示值保留，
      由归因阶段如实计入未映射。
    """
    if dimension_type is None:
        return list(records)
    other_values = {
        normalize_dimension_hint(value)
        for current_type, value in all_known_keys
        if current_type != dimension_type
    }
    target_values = {
        normalize_dimension_hint(value)
        for current_type, value in all_known_keys
        if current_type == dimension_type
    }
    kept: list[GapEvidenceRecord] = []
    for record in records:
        if record.dimension_type is not None:
            if record.dimension_type == dimension_type:
                kept.append(record)
        elif record.dimension_value is not None:
            normalized = normalize_dimension_hint(record.dimension_value)
            if normalized in other_values and normalized not in target_values:
                continue
            kept.append(record)
    return kept


__all__ = [
    "GapDimensionEvidence",
    "GapEvidenceAttribution",
    "GapEvidenceFacts",
    "GapEvidenceRecord",
    "GapEvidenceSource",
    "GapSourceCount",
    "GapUnmappedEvidence",
    "GapUnmappedReason",
    "OPEN_REQUIREMENT_STATUSES",
    "attribute_gap_evidence",
    "feedback_evidence",
    "filter_evidence_axis",
    "match_dimension_hint",
    "normalize_dimension_hint",
    "open_requirement_evidence",
]
