"""质检差异报告（V31-033）：领域判定。

断言的是**分类语义**，不是编排：应用层只负责取两次运行的 findings 并落库/
返回，分类规则必须在这里钉死，否则新增 ``DiffKind`` 时两处会漂移。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.quality.diff import DiffKind, diff_runs
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySeverity,
)
from app.domain.quality.models import QualityFinding


def _score_for(status: QualityFindingStatus) -> float | None:
    """PASS=1.0 / FAIL=0.0 / 其余（没判）为 None——领域强制要求。"""
    if status is QualityFindingStatus.PASS:
        return 1.0
    if status is QualityFindingStatus.FAIL:
        return 0.0
    return None


def _finding(
    *,
    rule_id: str = "label.required",
    subject_ref: str = "",
    status: QualityFindingStatus = QualityFindingStatus.PASS,
) -> QualityFinding:
    return QualityFinding(
        finding_id=uuid4(),
        project_id=uuid4(),
        run_id=uuid4(),
        rule_id=rule_id,
        rule_version="sha256:" + "a" * 64,
        category=QualityCategory.ANNOTATION,
        severity=QualitySeverity.ERROR,
        status=status,
        score=_score_for(status),
        message="规则结论",
        subject_ref=subject_ref,
        created_at=datetime(2026, 11, 22, tzinfo=UTC),
    )


def _diff(baseline, candidate):
    return diff_runs(
        baseline_run_id=uuid4(),
        candidate_run_id=uuid4(),
        baseline=tuple(baseline),
        candidate=tuple(candidate),
    )


def test_new_finding_when_only_candidate_has_it() -> None:
    result = _diff([], [_finding(status=QualityFindingStatus.FAIL)])

    assert len(result.findings) == 1
    assert result.findings[0].kind is DiffKind.NEW
    assert result.findings[0].baseline_status is None
    assert result.findings[0].candidate_status is QualityFindingStatus.FAIL
    assert result.has_regression is True


def test_resolved_finding_when_only_baseline_has_it() -> None:
    result = _diff([_finding(status=QualityFindingStatus.FAIL)], [])

    assert result.findings[0].kind is DiffKind.RESOLVED
    assert result.findings[0].baseline_status is QualityFindingStatus.FAIL
    assert result.findings[0].candidate_status is None
    assert result.has_regression is False


def test_unchanged_when_status_same() -> None:
    finding = _finding(status=QualityFindingStatus.FAIL)
    result = _diff([finding], [_finding(status=QualityFindingStatus.FAIL)])

    assert result.findings[0].kind is DiffKind.UNCHANGED
    assert result.has_regression is False


def test_changed_when_status_differs() -> None:
    result = _diff(
        [_finding(status=QualityFindingStatus.FAIL)],
        [_finding(status=QualityFindingStatus.PASS)],
    )

    assert result.findings[0].kind is DiffKind.CHANGED
    assert result.findings[0].baseline_status is QualityFindingStatus.FAIL
    assert result.findings[0].candidate_status is QualityFindingStatus.PASS
    # 修好不是回归。
    assert result.has_regression is False


def test_subject_ref_distinguishes_same_rule_on_different_objects() -> None:
    """同一规则作用于两条记录：一条通过、一条失败，不能被算成状态变化。"""
    result = _diff(
        [
            _finding(subject_ref="record-a", status=QualityFindingStatus.PASS),
            _finding(subject_ref="record-b", status=QualityFindingStatus.FAIL),
        ],
        [
            _finding(subject_ref="record-a", status=QualityFindingStatus.PASS),
            _finding(subject_ref="record-b", status=QualityFindingStatus.FAIL),
        ],
    )

    assert len(result.findings) == 2
    assert {item.kind for item in result.findings} == {DiffKind.UNCHANGED}
    assert result.counts[DiffKind.UNCHANGED] == 2


def test_not_applicable_is_not_treated_as_pass() -> None:
    """``NOT_APPLICABLE``（没判）与 ``PASS``（判过且通过）是不同结论。"""
    result = _diff(
        [_finding(status=QualityFindingStatus.NOT_APPLICABLE)],
        [_finding(status=QualityFindingStatus.PASS)],
    )

    assert result.findings[0].kind is DiffKind.CHANGED


def test_counts_cover_every_kind() -> None:
    result = _diff(
        [
            _finding(rule_id="a", status=QualityFindingStatus.FAIL),
            _finding(rule_id="b", status=QualityFindingStatus.PASS),
            _finding(rule_id="c", status=QualityFindingStatus.FAIL),
        ],
        [
            _finding(rule_id="b", status=QualityFindingStatus.PASS),
            _finding(rule_id="c", status=QualityFindingStatus.PASS),
            _finding(rule_id="d", status=QualityFindingStatus.FAIL),
        ],
    )

    assert result.counts == {
        DiffKind.NEW: 1,
        DiffKind.RESOLVED: 1,
        DiffKind.UNCHANGED: 1,
        DiffKind.CHANGED: 1,
    }


def test_duplicate_key_in_baseline_is_rejected() -> None:
    """同一侧重复键是数据缺陷：不猜该用哪一条，直接拒绝。"""
    with pytest.raises(ValueError, match="基线运行存在重复结论"):
        _diff([_finding(), _finding()], [])


def test_duplicate_key_in_candidate_is_rejected() -> None:
    with pytest.raises(ValueError, match="候选运行存在重复结论"):
        _diff([], [_finding(), _finding()])


def test_findings_are_sorted_by_key() -> None:
    result = _diff(
        [_finding(rule_id="z", subject_ref="b"), _finding(rule_id="a", subject_ref="b")],
        [_finding(rule_id="m", subject_ref="a")],
    )

    assert [item.key for item in result.findings] == [
        ("a", "b"),
        ("m", "a"),
        ("z", "b"),
    ]


def test_empty_runs_produce_empty_diff() -> None:
    result = _diff([], [])

    assert result.findings == ()
    assert result.has_regression is False
    assert all(count == 0 for count in result.counts.values())
