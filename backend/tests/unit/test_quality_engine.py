"""统一 Quality Engine 领域测试。

覆盖六类聚合、WARN 不阻断、未判定不能伪装通过、规则集内容版本与历史固化、
未知类别与重复声明拒绝。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import (
    QualityCategory,
    QualityCategoryStatus,
    QualityFindingStatus,
    QualityRunStatus,
    QualitySeverity,
    coerce_category,
)
from app.domain.quality.errors import DomainValidationError
from app.domain.quality.models import QualityFinding, QualityFindingDraft, QualityRun
from app.domain.quality.rules import (
    QualityRuleDeclaration,
    RuleSet,
    compute_rule_set_version,
)

PROJECT = uuid4()
RUN = uuid4()
NOW = datetime(2026, 11, 18, tzinfo=UTC)


def _finding(
    category: QualityCategory,
    status: QualityFindingStatus,
    *,
    severity: QualitySeverity = QualitySeverity.ERROR,
    rule_id: str = "rule-1",
    rule_version: str = "1",
) -> QualityFinding:
    score = None
    if status is QualityFindingStatus.PASS:
        score = 1.0
    elif status is QualityFindingStatus.FAIL:
        score = 0.0
    return QualityFinding(
        finding_id=uuid4(),
        project_id=PROJECT,
        run_id=RUN,
        rule_id=rule_id,
        rule_version=rule_version,
        category=category,
        severity=severity,
        status=status,
        message="判定说明",
        score=score,
        created_at=NOW,
    )


def _declaration(**overrides: object) -> QualityRuleDeclaration:
    payload: dict[str, object] = {
        "rule_id": "stage_rule",
        "category": QualityCategory.INTEGRITY,
        "version": "1.0.0",
        "severity": QualitySeverity.ERROR,
        "source": "unit_source",
        "description": "测试规则",
        "subject_types": ("EPISODE",),
    }
    payload.update(overrides)
    return QualityRuleDeclaration(**payload)  # type: ignore[arg-type]


def test_six_categories_are_always_present_and_error_fails_run() -> None:
    summary = summarize_findings(
        [
            _finding(QualityCategory.INTEGRITY, QualityFindingStatus.PASS, rule_id="a"),
            _finding(QualityCategory.TEMPORAL, QualityFindingStatus.FAIL, rule_id="b"),
        ]
    )

    payload = summary.category_payload()
    assert set(payload) == {item.value for item in QualityCategory}
    assert summary.status is QualityRunStatus.FAILED
    assert summary.categories[QualityCategory.TEMPORAL].status is QualityCategoryStatus.FAIL
    assert summary.categories[QualityCategory.OVERALL].status is QualityCategoryStatus.FAIL
    # 没有 finding 的类别必须显式 NOT_APPLICABLE，而不是消失。
    assert (
        summary.categories[QualityCategory.METADATA].status is QualityCategoryStatus.NOT_APPLICABLE
    )
    assert summary.error_count == 1
    assert summary.score == 0.5


def test_warn_failure_does_not_block_overall() -> None:
    summary = summarize_findings(
        [
            _finding(QualityCategory.VISUAL, QualityFindingStatus.PASS, rule_id="ok"),
            _finding(
                QualityCategory.ANNOTATION,
                QualityFindingStatus.FAIL,
                severity=QualitySeverity.WARN,
                rule_id="soft",
            ),
        ]
    )
    assert summary.categories[QualityCategory.ANNOTATION].status is QualityCategoryStatus.WARN
    assert summary.status is QualityRunStatus.PASSED
    assert summary.warn_count == 1
    assert summary.error_count == 0


def test_all_not_applicable_is_incomplete_not_passed() -> None:
    summary = summarize_findings(
        [
            _finding(QualityCategory.INTEGRITY, QualityFindingStatus.NOT_APPLICABLE, rule_id="a"),
            _finding(QualityCategory.ANNOTATION, QualityFindingStatus.NOT_APPLICABLE, rule_id="b"),
        ]
    )
    assert summary.status is QualityRunStatus.INCOMPLETE
    assert summary.score is None
    overall = summary.categories[QualityCategory.OVERALL]
    assert overall.status is QualityCategoryStatus.NOT_APPLICABLE


def test_not_applicable_must_not_carry_score() -> None:
    with pytest.raises(DomainValidationError):
        QualityFindingDraft(
            rule_id="r",
            rule_version="1",
            category=QualityCategory.INTEGRITY,
            severity=QualitySeverity.ERROR,
            status=QualityFindingStatus.NOT_APPLICABLE,
            message="没有判定对象",
            score=1.0,
        )
    with pytest.raises(DomainValidationError):
        QualityFindingDraft(
            rule_id="r",
            rule_version="1",
            category=QualityCategory.INTEGRITY,
            severity=QualitySeverity.ERROR,
            status=QualityFindingStatus.PASS,
            message="通过",
            score=None,
        )


def test_rule_set_version_is_content_addressed_and_stable() -> None:
    first = _declaration()
    same = _declaration()
    changed = replace(first, version="1.0.1")
    extra = replace(first, rule_id="stage_rule_v2")

    version = compute_rule_set_version([first])
    assert version == compute_rule_set_version([same])
    assert version != compute_rule_set_version([changed])
    assert version != compute_rule_set_version([first, extra])
    assert RuleSet((first,)).version == version


def test_historical_rule_version_is_not_overwritten_by_new_declaration() -> None:
    old = _declaration()
    new = replace(old, version="2.0.0")
    assert compute_rule_set_version([old]) != compute_rule_set_version([new])

    # 运行对象保存的是当时固化的版本；后续声明变化不会回写它。
    finding = _finding(
        QualityCategory.INTEGRITY,
        QualityFindingStatus.PASS,
        rule_id=old.rule_id,
        rule_version=old.version,
    )
    run = QualityRun(
        run_id=RUN,
        project_id=PROJECT,
        subject_type="EPISODE",
        subject_id=uuid4(),
        rule_set_version=compute_rule_set_version([old]),
        status=QualityRunStatus.PASSED,
        score=1.0,
        finding_count=1,
        error_count=0,
        warn_count=0,
        category_summary=summarize_findings([finding]).category_payload(),
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
        created_by=uuid4(),
        findings=(finding,),
    )
    assert run.rule_set_version == compute_rule_set_version([old])
    assert run.findings[0].rule_version == "1.0.0"


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        coerce_category("NOT_A_CATEGORY")
    with pytest.raises(DomainValidationError):
        QualityFindingDraft(
            rule_id="r",
            rule_version="1",
            category="NOT_A_CATEGORY",
            severity=QualitySeverity.ERROR,
            status=QualityFindingStatus.PASS,
            message="x",
            score=1.0,
        )


def test_overall_is_derived_and_cannot_be_declared() -> None:
    with pytest.raises(DomainValidationError):
        _declaration(category=QualityCategory.OVERALL)
    with pytest.raises(DomainValidationError):
        QualityFindingDraft(
            rule_id="r",
            rule_version="1",
            category=QualityCategory.OVERALL,
            severity=QualitySeverity.ERROR,
            status=QualityFindingStatus.PASS,
            message="x",
            score=1.0,
        )


def test_duplicate_declarations_are_rejected() -> None:
    first = _declaration()
    with pytest.raises(DomainValidationError):
        compute_rule_set_version([first, first])
