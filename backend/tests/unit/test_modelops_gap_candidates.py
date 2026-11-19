"""候选缺口（N7-005）的纯函数单测。

守住四条规则：

1. 提示值只做匹配，不创造维度；同一取值的不同写法聚合为同一候选；
2. 匹配不上/歧义的候选显式进入 ``unmapped``，不猜测、不丢弃；
3. 没有提示值的失败用例按类别聚合，且不能冒充 Data Gap 维度；
4. 返回有界，截断如实标记；候选键与输入顺序无关。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.modelops.enums import GapDimensionType
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.failure_cases import FailureCase
from app.domain.modelops.gap_candidates import (
    MAX_CANDIDATE_LIMIT,
    FailureCaseFact,
    build_gap_candidates,
    candidate_key_for,
)
from app.domain.modelops.gap_evidence import GapUnmappedReason

KNOWN = {("SCENE", "kitchen"), ("ACTION", "pick")}
EVAL_A = UUID(int=1)
EVAL_B = UUID(int=2)


def case(
    case_id: str,
    *,
    category: str = "PERCEPTION",
    severity: str = "MAJOR",
    suggested_gap: str | None = None,
) -> FailureCase:
    return FailureCase(
        id=case_id,
        category=category,
        severity=severity,
        suggested_gap=suggested_gap,
    )


def fact(evaluation_id: UUID, case_id: str, **kwargs) -> FailureCaseFact:
    return FailureCaseFact(evaluation_id=evaluation_id, case=case(case_id, **kwargs))


# --------------------------------------------------------------------------- #
# 聚合与去重
# --------------------------------------------------------------------------- #
def test_same_hint_with_different_casing_is_one_candidate() -> None:
    summary = build_gap_candidates(
        [
            fact(EVAL_A, "fc-1", suggested_gap="Kitchen"),
            fact(EVAL_A, "fc-2", suggested_gap="  kitchen "),
            fact(EVAL_B, "fc-3", suggested_gap="KITCHEN"),
        ],
        known_keys=KNOWN,
    )

    assert len(summary.items) == 1
    candidate = summary.items[0]
    assert candidate.dimension_type == GapDimensionType.SCENE.value
    assert candidate.dimension_value == "kitchen"
    assert candidate.failure_count == 3
    assert candidate.evaluation_count == 2
    assert candidate.source_evaluation_ids == (EVAL_A, EVAL_B)
    assert summary.unmapped == ()
    assert summary.items_truncated is False


def test_candidate_key_is_stable_across_casing_and_input_order() -> None:
    first = build_gap_candidates([fact(EVAL_A, "fc-1", suggested_gap="KiTcHeN")], known_keys=KNOWN)
    second = build_gap_candidates(
        [fact(EVAL_B, "fc-9", suggested_gap=" kitchen")], known_keys=KNOWN
    )

    assert first.items[0].candidate_key == second.items[0].candidate_key
    assert candidate_key_for("SUGGESTED_GAP", "KiTcHeN") == candidate_key_for(
        "SUGGESTED_GAP", " kitchen "
    )


def test_categories_and_severities_are_deduplicated_and_ordered() -> None:
    summary = build_gap_candidates(
        [
            fact(EVAL_A, "fc-1", category="PLANNING", severity="MINOR", suggested_gap="pick"),
            fact(EVAL_A, "fc-2", category="PERCEPTION", severity="BLOCKER", suggested_gap="pick"),
            fact(EVAL_A, "fc-3", category="PLANNING", severity="MAJOR", suggested_gap="pick"),
        ],
        known_keys=KNOWN,
    )

    candidate = summary.items[0]
    assert candidate.dimension_value == "pick"
    assert candidate.categories == ("PERCEPTION", "PLANNING")
    assert candidate.severities == ("BLOCKER", "MAJOR", "MINOR")


# --------------------------------------------------------------------------- #
# 未映射：不猜、不丢
# --------------------------------------------------------------------------- #
def test_unknown_hint_is_listed_as_unmapped_value_not_found() -> None:
    summary = build_gap_candidates(
        [fact(EVAL_A, "fc-1", suggested_gap="spaceship")], known_keys=KNOWN
    )

    assert summary.items == ()
    assert len(summary.unmapped) == 1
    assert summary.unmapped[0].unmapped_reason == GapUnmappedReason.VALUE_NOT_FOUND.value
    assert summary.unmapped[0].label == "spaceship"


def test_hint_matching_two_dimension_types_is_ambiguous() -> None:
    summary = build_gap_candidates(
        [fact(EVAL_A, "fc-1", suggested_gap="kitchen")],
        known_keys={("SCENE", "kitchen"), ("ACTION", "kitchen")},
    )

    assert summary.items == ()
    assert summary.unmapped[0].unmapped_reason == GapUnmappedReason.AMBIGUOUS_VALUE.value


def test_case_without_hint_becomes_category_candidate_not_dimension() -> None:
    summary = build_gap_candidates(
        [
            fact(EVAL_A, "fc-1", category="CONTROL", suggested_gap=None),
            fact(EVAL_B, "fc-2", category="CONTROL", suggested_gap=None),
        ],
        known_keys=KNOWN,
    )

    assert summary.items == ()
    assert len(summary.unmapped) == 1
    candidate = summary.unmapped[0]
    assert candidate.kind == "CATEGORY"
    assert candidate.label == "CONTROL"
    assert candidate.failure_count == 2
    assert candidate.evaluation_count == 2
    assert candidate.unmapped_reason == GapUnmappedReason.NO_DIMENSION.value


# --------------------------------------------------------------------------- #
# 边界
# --------------------------------------------------------------------------- #
def test_limit_truncates_each_list_and_marks_it() -> None:
    facts = [
        fact(EVAL_A, "fc-1", suggested_gap="kitchen"),
        fact(EVAL_A, "fc-2", suggested_gap="pick"),
        fact(EVAL_A, "fc-3", suggested_gap="warehouse"),
        fact(EVAL_A, "fc-4", category="PLANNING", suggested_gap=None),
        fact(EVAL_A, "fc-5", category="CONTROL", suggested_gap=None),
    ]

    summary = build_gap_candidates(facts, known_keys=KNOWN, limit=1)

    assert len(summary.items) == 1
    assert summary.items_truncated is True
    assert len(summary.unmapped) == 1
    assert summary.unmapped_truncated is True


def test_limit_above_maximum_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        build_gap_candidates([], known_keys=KNOWN, limit=MAX_CANDIDATE_LIMIT + 1)


def test_scan_flags_are_reported_not_hidden() -> None:
    summary = build_gap_candidates(
        [],
        known_keys=KNOWN,
        scan_truncated=True,
        evaluations_scanned=200,
        invalid_evaluation_count=2,
    )

    assert summary.scan_truncated is True
    assert summary.scanned_evaluation_count == 200
    assert summary.invalid_evaluation_count == 2


def test_fact_requires_failure_case_and_uuid() -> None:
    with pytest.raises(DomainValidationError):
        FailureCaseFact(evaluation_id=uuid4(), case="not-a-case")  # type: ignore[arg-type]
