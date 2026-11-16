"""质量检查领域对象的不变式：三态 ``passed``、计数一致性与规则集版本。

这些规则如果只靠集成测试保证，很容易在重构中被悄悄放宽——``passed=None``
一旦被折叠成 ``True``，界面上"没有结论"会显示成"检查通过"，这与伪造证据等价。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.annotation import (
    DomainValidationError,
    QualityCheckRun,
    QualityCheckStatus,
    QualityFinding,
    QualityRuleSeverity,
)
from app.domain.annotation.quality_check import QualityEvaluation, summarize_findings
from app.domain.annotation.quality_rules import (
    DEFAULT_RULES,
    QualityRule,
    RuleContext,
    RuleOutcome,
    default_registry,
    resolve_status,
)

SHA = "sha256:" + "a" * 64
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def _finding(
    *,
    passed: bool | None,
    severity: QualityRuleSeverity = QualityRuleSeverity.ERROR,
    rule_id: str = "required_labels_present",
) -> QualityFinding:
    return QualityFinding(
        annotation_id=uuid4(),
        rule_id=rule_id,
        rule_version=1,
        severity=severity,
        passed=passed,
        message="说明",
    )


def _run(*, findings: tuple[QualityFinding, ...], **overrides: object) -> QualityCheckRun:
    error_count, warn_count = summarize_findings(findings)
    payload: dict[str, object] = {
        "run_id": uuid4(),
        "project_id": uuid4(),
        "submission_id": uuid4(),
        "rule_set_version": SHA,
        "status": resolve_status(error_count),
        "error_count": error_count,
        "warn_count": warn_count,
        "started_at": NOW,
        "finished_at": NOW + timedelta(seconds=1),
        "findings": findings,
    }
    payload.update(overrides)
    return QualityCheckRun(**payload)  # type: ignore[arg-type]


def test_not_applicable_finding_is_neither_pass_nor_violation() -> None:
    finding = _finding(passed=None)
    assert finding.is_violation is False
    # 关键区分：None 不是 True。计数里两侧都不加。
    assert summarize_findings((finding,)) == (0, 0)


def test_only_error_severity_violations_count_as_errors() -> None:
    findings = (
        _finding(passed=False, severity=QualityRuleSeverity.ERROR, rule_id="a"),
        _finding(passed=False, severity=QualityRuleSeverity.WARN, rule_id="b"),
        _finding(passed=None, severity=QualityRuleSeverity.ERROR, rule_id="c"),
    )
    assert summarize_findings(findings) == (1, 1)
    assert resolve_status(1) is QualityCheckStatus.FAILED


def test_warn_only_violations_do_not_fail_the_run() -> None:
    run = _run(findings=(_finding(passed=False, severity=QualityRuleSeverity.WARN),))
    assert run.status is QualityCheckStatus.PASSED
    assert run.warn_count == 1


def test_run_rejects_counts_that_contradict_findings() -> None:
    findings = (_finding(passed=False),)
    with pytest.raises(DomainValidationError, match="不一致"):
        _run(findings=findings, error_count=0)
    with pytest.raises(DomainValidationError, match="status"):
        _run(findings=findings, status=QualityCheckStatus.PASSED)


def test_finished_at_must_not_precede_started_at() -> None:
    with pytest.raises(DomainValidationError, match="finished_at"):
        _run(findings=(), finished_at=NOW - timedelta(seconds=1))


def test_rule_set_version_must_be_sha256() -> None:
    with pytest.raises(DomainValidationError):
        _run(findings=(), rule_set_version="v1")


def test_evaluation_accepts_not_applicable_without_inflating_counts() -> None:
    evaluation = QualityEvaluation(
        findings=(_finding(passed=None),),
        error_count=0,
        warn_count=0,
        status=QualityCheckStatus.PASSED,
    )
    assert evaluation.status is QualityCheckStatus.PASSED


def test_default_registry_version_is_content_addressed_and_stable() -> None:
    first = default_registry().version
    second = default_registry().version
    assert first == second
    assert first.startswith("sha256:")

    # 规则版本变化必须改变规则集版本，否则历史结论无法解释"当时用的哪一版"。
    bumped = QualityRule(
        rule_id=DEFAULT_RULES[0].rule_id,
        version=DEFAULT_RULES[0].version + 1,
        severity=DEFAULT_RULES[0].severity,
        description=DEFAULT_RULES[0].description,
        evaluator=DEFAULT_RULES[0].evaluator,
    )
    changed = default_registry()
    changed.register(bumped)
    assert changed.version != first


def test_rule_rejects_evaluator_returning_wrong_type() -> None:
    rule = QualityRule(
        rule_id="broken",
        version=1,
        severity=QualityRuleSeverity.ERROR,
        description="返回错类型",
        evaluator=lambda record, context: "not-an-outcome",  # type: ignore[return-value,arg-type]
    )
    from app.domain.annotation.submission import AnnotationRecord

    record = AnnotationRecord(
        annotation_id=uuid4(),
        annotation_lineage_id=uuid4(),
        annotation_version=1,
        project_id=uuid4(),
        submission_id=uuid4(),
        episode_id=uuid4(),
        episode_version=1,
        task_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        labels=[{"label_key": "action", "value": "PICK"}],
    )
    with pytest.raises(DomainValidationError, match="RuleOutcome"):
        rule.evaluate(record, RuleContext())


def test_rule_outcome_rejects_non_boolean_passed() -> None:
    with pytest.raises(DomainValidationError):
        RuleOutcome(passed="yes", message="x")  # type: ignore[arg-type]
