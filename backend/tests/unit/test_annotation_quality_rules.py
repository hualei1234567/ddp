"""质量规则引擎领域单测（纯函数、无 IO）。

每条规则都覆盖正例、反例与"没有结论"三类；另有确定性与注册表约束用例。
"没有结论"（``applicable=False``）必须与"判定通过"区分开：它在下游落库为
``passed IS NULL``。若这里退化成 ``passed=True``，质量门禁会把"没检查"当成
"检查过且合规"，这正是 N4 要消除的误读。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.annotation.enums import (
    AnnotationLabelType,
    AnnotationLevel,
    LabelValueSource,
    QualityCheckStatus,
    QualityRuleSeverity,
)
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.quality_check import (
    QualityCheckRun,
    QualityFinding,
    summarize_findings,
)
from app.domain.annotation.quality_rules import (
    AI_CONFIDENCE_THRESHOLD_KEY,
    QualityRule,
    QualityRuleRegistry,
    RuleContext,
    RuleOutcome,
    default_registry,
    evaluate_ai_label_confidence,
    evaluate_duplicate_label_keys,
    evaluate_required_labels,
    evaluate_time_range_within_episode,
    resolve_status,
)
from app.domain.annotation.schema import LabelDefinition
from app.domain.annotation.submission import AnnotationRecord

SHA = "sha256:" + "a" * 64


def make_record(
    *,
    episode_id: UUID | None = None,
    labels: list[dict[str, object]] | None = None,
    time_range: dict[str, object] | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        annotation_id=uuid4(),
        annotation_lineage_id=uuid4(),
        annotation_version=1,
        project_id=uuid4(),
        submission_id=uuid4(),
        episode_id=episode_id or uuid4(),
        episode_version=1,
        task_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        labels=labels or [{"label_key": "action", "value": "PICK"}],
        time_range=time_range,
    )


def definition(
    label_key: str,
    *,
    required: bool,
    label_type: AnnotationLabelType = AnnotationLabelType.STRING,
) -> LabelDefinition:
    return LabelDefinition(
        label_key=label_key,
        label_type=label_type,
        required=required,
        level=AnnotationLevel.EPISODE,
    )


# ---------------------------------------------------------------------------
# 规则一：必填标签缺失
# ---------------------------------------------------------------------------


def test_required_labels_pass_when_every_required_key_present() -> None:
    record = make_record(
        labels=[
            {"label_key": "action", "value": "PICK"},
            {"label_key": "note", "value": "ok"},
        ]
    )
    context = RuleContext(
        definitions=(definition("action", required=True), definition("note", required=False))
    )

    outcome = evaluate_required_labels(record, context)

    assert outcome.passed is True
    assert outcome.applicable is True
    assert outcome.evidence is None


def test_required_labels_fail_and_list_every_missing_key_sorted() -> None:
    record = make_record(labels=[{"label_key": "action", "value": "PICK"}])
    context = RuleContext(
        definitions=(
            definition("zeta", required=True),
            definition("alpha", required=True),
            definition("action", required=True),
        )
    )

    outcome = evaluate_required_labels(record, context)

    assert outcome.passed is False
    assert outcome.evidence == {"missing_label_keys": ["alpha", "zeta"]}


def test_required_labels_without_definitions_is_not_applicable() -> None:
    outcome = evaluate_required_labels(make_record(), RuleContext())

    assert outcome.applicable is False
    # 关键：不适用时 passed 不得被下游当作"合规证据"，这里取值不影响落库，
    # 但必须明确标记 applicable=False。
    assert "未提供" in outcome.message


# ---------------------------------------------------------------------------
# 规则二：时间片段越界
# ---------------------------------------------------------------------------


def test_time_range_inside_episode_bounds_passes() -> None:
    episode_id = uuid4()
    record = make_record(
        episode_id=episode_id,
        time_range={"start_time_ns": 1_200, "end_time_ns": 1_800, "time_domain": "UTC"},
    )
    context = RuleContext(episode_bounds={episode_id: (1_000, 2_000)})

    outcome = evaluate_time_range_within_episode(record, context)

    assert outcome.passed is True
    assert outcome.evidence == {
        "episode_start_time_ns": 1_000,
        "episode_end_time_ns": 2_000,
        "record_start_time_ns": 1_200,
        "record_end_time_ns": 1_800,
    }


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (999, 1_500),  # 起点早于 Episode 起点
        (1_500, 2_001),  # 终点晚于 Episode 终点
        (900, 2_100),  # 两端同时越界
    ],
)
def test_time_range_outside_episode_bounds_fails(start: int, end: int) -> None:
    episode_id = uuid4()
    record = make_record(
        episode_id=episode_id,
        time_range={"start_time_ns": start, "end_time_ns": end, "time_domain": "UTC"},
    )

    outcome = evaluate_time_range_within_episode(
        record,
        RuleContext(episode_bounds={episode_id: (1_000, 2_000)}),
    )

    assert outcome.passed is False
    assert outcome.evidence is not None
    assert outcome.evidence["record_start_time_ns"] == start


def test_time_range_exact_boundaries_pass() -> None:
    """边界是闭区间：恰好等于 Episode 起止点不算越界。"""
    episode_id = uuid4()
    record = make_record(
        episode_id=episode_id,
        time_range={"start_time_ns": 1_000, "end_time_ns": 2_000, "time_domain": "UTC"},
    )

    outcome = evaluate_time_range_within_episode(
        record,
        RuleContext(episode_bounds={episode_id: (1_000, 2_000)}),
    )

    assert outcome.passed is True


def test_time_range_without_episode_bounds_is_not_applicable() -> None:
    record = make_record(
        time_range={"start_time_ns": 1_200, "end_time_ns": 1_800, "time_domain": "UTC"}
    )

    outcome = evaluate_time_range_within_episode(record, RuleContext())

    assert outcome.applicable is False
    assert outcome.passed is True  # 取值无含义，但不得抛错


def test_time_range_absent_from_record_is_not_applicable() -> None:
    outcome = evaluate_time_range_within_episode(
        make_record(),
        RuleContext(episode_bounds={uuid4(): (0, 1)}),
    )

    assert outcome.applicable is False


# ---------------------------------------------------------------------------
# 规则三：AI 标签置信度过低
# ---------------------------------------------------------------------------


def _ai_label(label_key: str, confidence: float | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "label_key": label_key,
        "value": "PICK",
        "source": LabelValueSource.AI_ASSISTED.value,
    }
    if confidence is not None:
        payload["confidence"] = confidence
    return payload


def test_ai_confidence_without_policy_threshold_is_not_applicable() -> None:
    record = make_record(labels=[_ai_label("action", 0.1)])

    outcome = evaluate_ai_label_confidence(record, RuleContext())

    assert outcome.applicable is False
    assert AI_CONFIDENCE_THRESHOLD_KEY in outcome.message


def test_ai_confidence_no_ai_labels_passes() -> None:
    record = make_record(labels=[{"label_key": "action", "value": "PICK"}])
    context = RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8})

    outcome = evaluate_ai_label_confidence(record, context)

    assert outcome.passed is True
    assert outcome.applicable is True


def test_ai_confidence_above_threshold_passes() -> None:
    record = make_record(labels=[_ai_label("action", 0.85)])
    context = RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8})

    outcome = evaluate_ai_label_confidence(record, context)

    assert outcome.passed is True
    assert outcome.evidence == {"threshold": 0.8}


def test_ai_confidence_below_threshold_fails_with_evidence() -> None:
    record = make_record(
        labels=[_ai_label("action", 0.5), _ai_label("object", 0.79)],
    )
    context = RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8})

    outcome = evaluate_ai_label_confidence(record, context)

    assert outcome.passed is False
    assert outcome.evidence is not None
    assert outcome.evidence["threshold"] == 0.8
    assert [item["label_key"] for item in outcome.evidence["below_threshold"]] == [
        "action",
        "object",
    ]


def test_ai_confidence_exactly_at_threshold_passes() -> None:
    record = make_record(labels=[_ai_label("action", 0.8)])

    outcome = evaluate_ai_label_confidence(
        record,
        RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8}),
    )

    assert outcome.passed is True


def test_ai_confidence_missing_on_ai_label_fails() -> None:
    """缺置信度不能当作"无法证明不达标"放过。"""
    record = make_record(labels=[_ai_label("action", None)])

    outcome = evaluate_ai_label_confidence(
        record,
        RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8}),
    )

    assert outcome.passed is False
    assert outcome.evidence is not None
    assert outcome.evidence["missing_confidence"] == ["action"]


def test_human_label_confidence_is_ignored() -> None:
    record = make_record(
        labels=[{"label_key": "action", "value": "PICK", "source": "HUMAN", "confidence": 0.1}]
    )

    outcome = evaluate_ai_label_confidence(
        record,
        RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.8}),
    )

    assert outcome.passed is True


@pytest.mark.parametrize("bad", [True, "0.8", -0.1, 1.1])
def test_invalid_policy_threshold_raises_instead_of_falling_back(bad: object) -> None:
    """策略值非法必须报错：静默回退默认值会让配置错误伪装成"已检查"。"""
    with pytest.raises(DomainValidationError):
        evaluate_ai_label_confidence(
            make_record(labels=[_ai_label("action", 0.5)]),
            RuleContext(policy={AI_CONFIDENCE_THRESHOLD_KEY: bad}),
        )


# ---------------------------------------------------------------------------
# 规则四：重复标签键
# ---------------------------------------------------------------------------


def test_duplicate_label_keys_none_passes() -> None:
    record = make_record(
        labels=[
            {"label_key": "action", "value": "PICK"},
            {"label_key": "object", "value": "cup"},
        ]
    )

    outcome = evaluate_duplicate_label_keys(record, RuleContext())

    assert outcome.passed is True


def test_duplicate_label_keys_case_and_space_folded() -> None:
    """``action`` 与 `` Action `` 是同一语义键，必须判为重复。"""
    record = make_record(
        labels=[
            {"label_key": "action", "value": "PICK"},
            {"label_key": " Action ", "value": "PLACE"},
        ]
    )

    outcome = evaluate_duplicate_label_keys(record, RuleContext())

    assert outcome.passed is False
    # 实体层已 trim 标签键，因此这里看到的是 "Action" 而非 " Action "。
    assert outcome.evidence == {
        "duplicates": [{"normalized_key": "action", "label_keys": ["Action", "action"]}]
    }


# ---------------------------------------------------------------------------
# 规则与注册表约束
# ---------------------------------------------------------------------------


def test_default_registry_has_four_sorted_rules_with_stable_version() -> None:
    registry = default_registry()
    ids = [rule.rule_id for rule in registry.list_rules()]

    assert ids == sorted(ids)
    assert len(registry) == 4
    assert registry.version == default_registry().version
    assert registry.version.startswith("sha256:")
    assert len(registry.version) == 71


def test_registry_version_changes_when_rule_version_changes() -> None:
    baseline = default_registry().version
    changed = default_registry()
    original = changed.get("duplicate_label_keys")
    changed.register(
        QualityRule(
            rule_id=original.rule_id,
            version=original.version + 1,
            severity=original.severity,
            description=original.description,
            evaluator=original.evaluator,
        )
    )

    assert changed.version != baseline


def test_registry_rejects_same_version_twice() -> None:
    registry = default_registry()
    rule = registry.get("required_labels_present")

    with pytest.raises(DomainValidationError):
        registry.register(rule)


def test_registry_rejects_version_downgrade() -> None:
    registry = QualityRuleRegistry()
    rule = QualityRule(
        rule_id="demo",
        version=2,
        severity=QualityRuleSeverity.ERROR,
        description="演示规则",
        evaluator=lambda record, context: RuleOutcome(passed=True, message="ok"),
    )
    registry.register(rule)

    with pytest.raises(DomainValidationError):
        registry.register(
            QualityRule(
                rule_id="demo",
                version=1,
                severity=QualityRuleSeverity.ERROR,
                description="演示规则旧版",
                evaluator=rule.evaluator,
            )
        )


def test_registry_get_unknown_rule_raises() -> None:
    with pytest.raises(DomainValidationError):
        default_registry().get("no_such_rule")


def test_rule_must_return_rule_outcome() -> None:
    """规则返回错类型属于编程错误，必须失败而不是被当成"通过"。"""
    registry = QualityRuleRegistry(
        (
            QualityRule(
                rule_id="broken",
                version=1,
                severity=QualityRuleSeverity.ERROR,
                description="返回错类型",
                evaluator=lambda record, context: {"passed": True},
            ),
        )
    )

    with pytest.raises(DomainValidationError):
        registry.get("broken").evaluate(make_record(), RuleContext())


def test_rules_are_deterministic_across_repeated_evaluation() -> None:
    """同一输入重复求值必须逐字一致（含 evidence 的键序与排序）。"""
    episode_id = uuid4()
    record = make_record(
        episode_id=episode_id,
        labels=[_ai_label("action", 0.2), {"label_key": "Action", "value": "PICK"}],
        time_range={"start_time_ns": 1, "end_time_ns": 2, "time_domain": "UTC"},
    )
    context = RuleContext(
        definitions=(definition("action", required=True), definition("zzz", required=True)),
        episode_bounds={episode_id: (1_000, 2_000)},
        policy={AI_CONFIDENCE_THRESHOLD_KEY: 0.9},
    )
    registry = default_registry()

    first = [(rule.rule_id, rule.evaluate(record, context)) for rule in registry.list_rules()]
    second = [(rule.rule_id, rule.evaluate(record, context)) for rule in registry.list_rules()]

    assert first == second


def test_rule_severity_and_outcome_validation() -> None:
    with pytest.raises(DomainValidationError):
        QualityRule(
            rule_id="x",
            version=1,
            severity="FATAL",
            description="非法严重级别",
            evaluator=lambda record, context: RuleOutcome(passed=True, message="ok"),
        )
    with pytest.raises(DomainValidationError):
        RuleOutcome(passed="yes", message="ok")  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        RuleOutcome(passed=True, message="   ")


# ---------------------------------------------------------------------------
# 汇总与结论
# ---------------------------------------------------------------------------


def test_resolve_status_only_counts_errors() -> None:
    assert resolve_status(0) is QualityCheckStatus.PASSED
    assert resolve_status(1) is QualityCheckStatus.FAILED
    with pytest.raises(DomainValidationError):
        resolve_status(-1)


def test_summarize_findings_ignores_not_applicable() -> None:
    findings = (
        QualityFinding(
            annotation_id=uuid4(),
            rule_id="a",
            rule_version=1,
            severity=QualityRuleSeverity.ERROR,
            passed=False,
            message="违规",
        ),
        QualityFinding(
            annotation_id=uuid4(),
            rule_id="b",
            rule_version=1,
            severity=QualityRuleSeverity.WARN,
            passed=None,
            message="没有结论",
        ),
        QualityFinding(
            annotation_id=uuid4(),
            rule_id="c",
            rule_version=1,
            severity=QualityRuleSeverity.WARN,
            passed=False,
            message="告警",
        ),
    )

    assert summarize_findings(findings) == (1, 1)


def _error_finding() -> QualityFinding:
    return QualityFinding(
        annotation_id=uuid4(),
        rule_id="required_labels_present",
        rule_version=1,
        severity=QualityRuleSeverity.ERROR,
        passed=False,
        message="缺少必填标签",
    )


def test_quality_check_run_accepts_counts_derived_from_findings() -> None:
    """正例必须被接受：若实现改成"计数恒 0/恒忽略明细"，这条会先失败。

    只有负例（"不一致要报错"）时，把所有计数校验都退化成常量也能通过，
    因此正例是不可省的。
    """
    now = datetime.now(UTC)
    run = QualityCheckRun(
        run_id=uuid4(),
        project_id=uuid4(),
        submission_id=uuid4(),
        rule_set_version=SHA,
        status=QualityCheckStatus.FAILED,
        error_count=1,
        warn_count=0,
        started_at=now,
        finished_at=now,
        findings=(_error_finding(),),
    )

    assert run.error_count == 1
    assert run.status is QualityCheckStatus.FAILED
    assert len(run.findings) == 1


def test_quality_check_run_rejects_wrong_error_count_for_findings() -> None:
    """明细里有 1 条 ERROR 违规，却声明 0 个错误：必须拒绝。"""
    now = datetime.now(UTC)
    with pytest.raises(DomainValidationError):
        QualityCheckRun(
            run_id=uuid4(),
            project_id=uuid4(),
            submission_id=uuid4(),
            rule_set_version=SHA,
            status=QualityCheckStatus.PASSED,
            error_count=0,
            warn_count=0,
            started_at=now,
            finished_at=now,
            findings=(_error_finding(),),
        )


def test_quality_check_run_rejects_inconsistent_counts() -> None:
    """计数与明细不一致时必须在构造期失败，不能落库后再靠人发现。"""
    now = datetime.now(UTC)
    with pytest.raises(DomainValidationError):
        QualityCheckRun(
            run_id=uuid4(),
            project_id=uuid4(),
            submission_id=uuid4(),
            rule_set_version=SHA,
            status=QualityCheckStatus.PASSED,
            error_count=1,
            warn_count=0,
            started_at=now,
            finished_at=now,
        )
