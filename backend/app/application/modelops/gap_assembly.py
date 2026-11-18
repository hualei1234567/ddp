"""缺口视图的组装：把反馈、需求、实测覆盖与多来源证据合成一个只读视图。

放在独立模块的原因：「取哪些数据」由用例负责（仓库/端口），「怎么合并」是一条
完整规则（既有维度键 + 来源归因 + 未映射 + 截断）且被单测直接覆盖；继续堆在
``feedback.py`` 里会把该文件推过 500 行阈值，也不利于单独审查归因口径。

本模块不访问数据库、不读时钟以外的外部状态，所有输入由调用方传入。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta

from app.domain.modelops.enums import RequirementStatus
from app.domain.modelops.gap_evidence import (
    GapEvidenceAttribution,
    GapEvidenceFacts,
    GapEvidenceSource,
    attribute_gap_evidence,
    feedback_evidence,
    filter_evidence_axis,
    open_requirement_evidence,
)
from app.domain.modelops.gaps import (
    GapSummary,
    GapSummaryItem,
    MeasuredCoverage,
    aggregate_gaps,
    build_gap_summary,
)
from app.domain.modelops.models import DataRequirement, ModelFeedback


def requirement_links(
    requirements: Iterable[DataRequirement],
) -> dict[tuple[str, str], list[str]]:
    """把需求挂到维度键上；语义与改造前一致（只排除撤销/驳回）。

    注意与来源计数口径的区别：``requirement_ids`` 是"与该维度相关的需求"，
    已满足/已关闭的需求仍保留历史关联；``REQUIREMENT`` 来源计数只算**仍未满足**
    的需求（见 ``open_requirement_evidence``）。两者刻意不同，不可互相替代。
    """
    links: dict[tuple[str, str], list[str]] = {}
    for requirement in requirements:
        if requirement.status in {
            RequirementStatus.CANCELLED.value,
            RequirementStatus.REJECTED.value,
        }:
            continue
        for dimension in requirement.dimensions:
            key = (dimension.dimension_type, dimension.dimension_value)
            links.setdefault(key, []).append(str(requirement.requirement_id))
    return links


def build_gap_view(
    *,
    feedback_items: list[ModelFeedback],
    requirements: list[DataRequirement],
    measured: list[MeasuredCoverage],
    evidence_facts: GapEvidenceFacts | None,
    dimension_type: str | None,
    now: datetime,
    stale_after: timedelta | None,
    requirement_scan_limit: int,
) -> GapSummary:
    """合成缺口视图。

    不变量：
    - ``current_count`` / ``suggested_episode_count`` 只由 ``measured`` 与反馈
      人工值决定，证据不参与；
    - 证据只挂到既有维度键上，挂不上的进 ``unmapped``；
    - 需求或证据扫描触达上限时，在 ``truncated_sources`` 里如实声明。
    """
    items = aggregate_gaps(
        [item.dimensions for item in feedback_items],
        requirement_links(requirements),
        measured_coverage=measured,
    )
    all_known_keys = [(item.dimension_type, item.dimension_value) for item in items]
    records = feedback_evidence(
        [
            (dimension.dimension_type, dimension.dimension_value)
            for item in feedback_items
            for dimension in item.dimensions
        ],
        dimension_type=dimension_type,
    )
    records.extend(open_requirement_evidence(requirements, dimension_type=dimension_type))
    truncated: list[str] = []
    if len(requirements) >= requirement_scan_limit:
        truncated.append(GapEvidenceSource.REQUIREMENT.value)
    if evidence_facts is not None:
        truncated.extend(evidence_facts.truncated_sources)
        records.extend(
            filter_evidence_axis(
                evidence_facts.records,
                dimension_type=dimension_type,
                all_known_keys=all_known_keys,
            )
        )
    known_keys = [
        key for key in all_known_keys if dimension_type is None or key[0] == dimension_type
    ]
    attribution = attribute_gap_evidence(records, known_keys)
    items = [_with_sources(item, attribution) for item in items]
    return build_gap_summary(
        items,
        measured_coverage=measured,
        now=now,
        stale_after=stale_after,
        unmapped=attribution.unmapped,
        truncated_sources=tuple(dict.fromkeys(truncated)),
    )


def _with_sources(item: GapSummaryItem, attribution: GapEvidenceAttribution) -> GapSummaryItem:
    return replace(
        item,
        sources=attribution.sources_for(item.dimension_type, item.dimension_value),
    )


__all__ = ["build_gap_view", "requirement_links"]
