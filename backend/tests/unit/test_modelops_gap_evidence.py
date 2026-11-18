"""数据缺口多来源证据（N7-004）的纯函数单测。

守住三条规则：

1. 证据只挂到**既有**维度键上，不凭空造维度；
2. 挂不上的条目进入未映射汇总（含原因），不丢弃、不瞎猜；
3. 证据不改变覆盖口径：``current_count`` / ``suggested_episode_count`` 仍只由
   实测覆盖与人工值决定。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.modelops.enums import RequirementStatus
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.gap_evidence import (
    GapEvidenceRecord,
    GapEvidenceSource,
    GapSourceCount,
    GapUnmappedReason,
    attribute_gap_evidence,
    feedback_evidence,
    filter_evidence_axis,
    open_requirement_evidence,
)
from app.domain.modelops.gaps import (
    GapDimension,
    GapSummary,
    GapSummaryItem,
    MeasuredCoverage,
    aggregate_gaps,
    build_gap_summary,
)
from app.domain.modelops.models import new_requirement

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
OWNER = UUID(int=9)

KNOWN = {
    ("SCENE", "kitchen"),
    ("SCENE", "warehouse"),
    ("ACTION", "pick"),
    ("LABEL", "action"),
}


def _requirement(*dimensions: GapDimension, status: str = RequirementStatus.DRAFT.value):
    return new_requirement(
        requirement_id=uuid4(),
        project_id=uuid4(),
        code=f"DR-{uuid4().hex[:8]}",
        title="补数需求",
        source_type="MANUAL",
        dimensions=list(dimensions),
        owner_id=OWNER,
        target_episode_count=1,
        description=None,
        source_feedback_ids=None,
        source_evaluation_ids=None,
        quality_thresholds=None,
        priority="P1",
        due_at=None,
        actor_id=OWNER,
        now=NOW,
    )


# --------------------------------------------------------------------------- #
# 归因
# --------------------------------------------------------------------------- #
def test_typed_evidence_attaches_to_exact_known_key() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                "ANNOTATION_CONFLICT",
                count=2,
                dimension_type="LABEL",
                dimension_value="action",
            ),
            GapEvidenceRecord(
                "ANNOTATION_CONFLICT",
                count=1,
                dimension_type="LABEL",
                dimension_value="action",
            ),
        ],
        KNOWN,
    )
    assert attribution.sources_for("LABEL", "action") == (GapSourceCount("ANNOTATION_CONFLICT", 3),)
    assert attribution.unmapped == ()


def test_typed_evidence_for_unknown_key_is_unmapped_not_created() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                GapEvidenceSource.REQUIREMENT.value,
                count=1,
                dimension_type="SCENE",
                dimension_value="moon",
            )
        ],
        KNOWN,
    )
    assert attribution.entries == ()
    assert len(attribution.unmapped) == 1
    assert attribution.unmapped[0].source == "REQUIREMENT"
    assert attribution.unmapped[0].reason == GapUnmappedReason.VALUE_NOT_FOUND.value
    assert attribution.unmapped[0].count == 1


def test_hint_value_matches_after_trim_and_casefold() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                GapEvidenceSource.FAILURE_CASE.value,
                count=4,
                dimension_value="  KiTcHeN ",
            )
        ],
        KNOWN,
    )
    assert attribution.sources_for("SCENE", "kitchen") == (GapSourceCount("FAILURE_CASE", 4),)
    assert attribution.unmapped == ()


def test_hint_value_matching_multiple_types_is_ambiguous() -> None:
    known = {("SCENE", "pick"), ("ACTION", "pick")}
    attribution = attribute_gap_evidence(
        [GapEvidenceRecord(GapEvidenceSource.FAILURE_CASE.value, count=1, dimension_value="pick")],
        known,
    )
    assert attribution.entries == ()
    assert attribution.unmapped[0].reason == GapUnmappedReason.AMBIGUOUS_VALUE.value


def test_hint_value_without_match_is_unmapped() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                GapEvidenceSource.FAILURE_CASE.value,
                count=2,
                dimension_value="spaceship",
            )
        ],
        KNOWN,
    )
    assert attribution.entries == ()
    assert attribution.unmapped[0].reason == GapUnmappedReason.VALUE_NOT_FOUND.value
    assert attribution.unmapped[0].count == 2


def test_evidence_without_dimension_is_unmapped_with_declared_reason() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                GapEvidenceSource.LOW_SCORE.value,
                count=3,
                reason=GapUnmappedReason.NO_DIMENSION.value,
            ),
            GapEvidenceRecord(
                GapEvidenceSource.FAILURE_CASE.value,
                count=1,
                reason=GapUnmappedReason.INVALID_EVIDENCE.value,
            ),
        ],
        KNOWN,
    )
    assert attribution.entries == ()
    by_reason = {item.reason: item for item in attribution.unmapped}
    assert by_reason["NO_DIMENSION"].count == 3
    assert by_reason["INVALID_EVIDENCE"].count == 1


def test_sources_are_sorted_in_declared_order() -> None:
    attribution = attribute_gap_evidence(
        [
            GapEvidenceRecord(
                "REQUIREMENT", count=1, dimension_type="SCENE", dimension_value="kitchen"
            ),
            GapEvidenceRecord("FAILURE_CASE", count=2, dimension_value="kitchen"),
            GapEvidenceRecord(
                "FEEDBACK", count=1, dimension_type="SCENE", dimension_value="kitchen"
            ),
        ],
        KNOWN,
    )
    assert [item.source for item in attribution.sources_for("SCENE", "kitchen")] == [
        "FEEDBACK",
        "REQUIREMENT",
        "FAILURE_CASE",
    ]


def test_empty_evidence_yields_empty_attribution() -> None:
    attribution = attribute_gap_evidence([], KNOWN)
    assert attribution.entries == ()
    assert attribution.unmapped == ()
    assert attribution.sources_for("SCENE", "kitchen") == ()


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source": "NOPE", "count": 1}, "source"),
        ({"source": "FEEDBACK", "count": 0}, "count"),
        (
            {"source": "FEEDBACK", "count": 1, "dimension_type": "SCENE"},
            "维度值",
        ),
        ({"source": "LOW_SCORE", "count": 1}, "未映射原因"),
        (
            {
                "source": "FAILURE_CASE",
                "count": 1,
                "dimension_value": "kitchen",
                "reason": "NO_DIMENSION",
            },
            "不能预设",
        ),
    ],
)
def test_invalid_evidence_record_is_rejected(kwargs, message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        GapEvidenceRecord(**kwargs)


# --------------------------------------------------------------------------- #
# 来源构造
# --------------------------------------------------------------------------- #
def test_feedback_evidence_counts_each_feedback_dimension() -> None:
    records = feedback_evidence(
        [("SCENE", "kitchen"), ("SCENE", "kitchen"), ("ACTION", "pick")],
        dimension_type="SCENE",
    )
    assert [(item.source, item.dimension_value, item.count) for item in records] == [
        ("FEEDBACK", "kitchen", 1),
        ("FEEDBACK", "kitchen", 1),
    ]


def test_requirement_evidence_only_counts_open_requirements() -> None:
    open_requirement = _requirement(GapDimension("SCENE", "kitchen"))
    satisfied = replace(
        _requirement(GapDimension("SCENE", "warehouse")),
        status=RequirementStatus.SATISFIED.value,
    )
    cancelled = replace(
        _requirement(GapDimension("ACTION", "pick")),
        status=RequirementStatus.CANCELLED.value,
    )
    records = open_requirement_evidence([open_requirement, satisfied, cancelled])
    assert [(item.dimension_value, item.count) for item in records] == [("kitchen", 1)]
    assert records[0].source == "REQUIREMENT"


def test_requirement_evidence_can_be_filtered_to_one_axis() -> None:
    requirement = _requirement(GapDimension("SCENE", "kitchen"), GapDimension("ACTION", "pick"))
    records = open_requirement_evidence([requirement], dimension_type="ACTION")
    assert [item.dimension_value for item in records] == ["pick"]


# --------------------------------------------------------------------------- #
# 单轴视图过滤
# --------------------------------------------------------------------------- #
def test_axis_filter_keeps_hints_and_drops_other_axes_and_project_level_facts() -> None:
    records = [
        GapEvidenceRecord("FEEDBACK", count=1, dimension_type="SCENE", dimension_value="kitchen"),
        GapEvidenceRecord("FEEDBACK", count=1, dimension_type="ACTION", dimension_value="pick"),
        GapEvidenceRecord("FAILURE_CASE", count=1, dimension_value="kitchen"),
        GapEvidenceRecord("LOW_SCORE", count=1, reason=GapUnmappedReason.NO_DIMENSION.value),
    ]
    kept = filter_evidence_axis(records, dimension_type="SCENE")
    assert [item.dimension_value for item in kept] == ["kitchen", "kitchen"]
    assert [item.source for item in kept] == ["FEEDBACK", "FAILURE_CASE"]
    assert filter_evidence_axis(records, dimension_type=None) == records


def test_axis_filter_drops_hints_that_clearly_belong_to_another_axis() -> None:
    records = [
        GapEvidenceRecord("FAILURE_CASE", count=1, dimension_value="kitchen"),
        GapEvidenceRecord("FAILURE_CASE", count=1, dimension_value="pick"),
    ]
    kept = filter_evidence_axis(
        records,
        dimension_type="SCENE",
        all_known_keys=[("SCENE", "kitchen"), ("ACTION", "pick")],
    )
    # kitchen 命中目标轴 → 保留；pick 只命中 ACTION → 丢弃，不报成 SCENE 的未映射。
    assert [item.dimension_value for item in kept] == ["kitchen"]


# --------------------------------------------------------------------------- #
# 缺口条目与摘要
# --------------------------------------------------------------------------- #
def test_summary_item_carries_sources_without_changing_coverage() -> None:
    items = aggregate_gaps(
        [[GapDimension("SCENE", "kitchen", current_count=2, target_count=10)]],
        {},
        measured_coverage=[MeasuredCoverage("SCENE", "kitchen", count=6, measured_at=NOW)],
    )
    attribution = attribute_gap_evidence(
        [GapEvidenceRecord("FAILURE_CASE", count=2, dimension_value="kitchen")],
        [(item.dimension_type, item.dimension_value) for item in items],
    )
    enriched = [
        replace(item, sources=attribution.sources_for(item.dimension_type, item.dimension_value))
        for item in items
    ]
    summary = build_gap_summary(
        enriched,
        measured_coverage=[MeasuredCoverage("SCENE", "kitchen", count=6, measured_at=NOW)],
        now=NOW,
        stale_after=None,
        unmapped=attribution.unmapped,
        truncated_sources=("REQUIREMENT",),
    )
    item = summary.items[0]
    # 覆盖口径不变：实测优先，证据不参与计数。
    assert item.coverage_source == "MEASURED"
    assert item.current_count == 6
    assert item.suggested_episode_count == 4
    assert item.to_payload()["sources"] == [{"source": "FAILURE_CASE", "count": 2}]
    assert summary.unmapped == ()
    assert summary.truncated_sources == ("REQUIREMENT",)


def test_summary_item_defaults_to_no_sources() -> None:
    item = GapSummaryItem(
        dimension_type="SCENE",
        dimension_value="kitchen",
        open_feedback_count=1,
        current_count=None,
        target_count=3,
        suggested_episode_count=0,
        requirement_ids=(),
    )
    assert item.sources == ()
    assert item.to_payload()["sources"] == []


def test_summary_rejects_unknown_source() -> None:
    with pytest.raises(DomainValidationError):
        GapSummaryItem(
            dimension_type="SCENE",
            dimension_value="kitchen",
            open_feedback_count=1,
            current_count=None,
            target_count=3,
            suggested_episode_count=0,
            requirement_ids=(),
            sources=({"source": "NOPE", "count": 1},),
        )


def test_gap_summary_keeps_unmapped_and_truncation() -> None:
    summary = GapSummary(
        items=(),
        unmapped=(),
        truncated_sources=("FAILURE_CASE",),
    )
    assert summary.freshness == "UNKNOWN"
    assert summary.truncated_sources == ("FAILURE_CASE",)
