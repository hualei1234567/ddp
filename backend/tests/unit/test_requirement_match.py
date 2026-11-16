"""TRN-002 需求匹配领域单测。

覆盖三条设计约束：
1. 三态语义 —— ``UNVERIFIABLE`` 绝不等于 ``PASSED``；
2. 每个可核对维度的正例 / 反例 / 无法核对；
3. 确定性 —— 同输入同输出；快照缺字段不崩。
"""

from __future__ import annotations

import pytest
from app.domain.datasets.requirement_match import (
    EnforcementPolicy,
    MatchDimension,
    MatchPolicy,
    MatchStatus,
    VersionFacts,
    evaluate_requirement_match,
)

POLICY = MatchPolicy(
    on_failed=EnforcementPolicy.BLOCK,
    on_unverifiable=EnforcementPolicy.WARN,
    split_ratio_tolerance=0.02,
)


def _result_by_dimension(report, dimension: MatchDimension):
    matches = [item for item in report.results if item.dimension is dimension]
    assert len(matches) == 1, f"{dimension} 应恰好出现一次"
    return matches[0]


def _full_facts(**overrides: object) -> VersionFacts:
    base: dict[str, object] = {
        "selected_count": 10,
        "total_duration_ns": 1_000_000,
        "split_counts": {"train": 8, "validation": 1, "test": 1},
        "scene_labels": frozenset({"kitchen"}),
        "annotation_labels": {"action": frozenset({"pick"})},
        "annotation_schema_versions": frozenset({("schema-1", 1)}),
        "data_modalities": frozenset({"RGB", "PROPRIOCEPTION"}),
        "quality_metrics": {
            "selected_count": 10,
            "duplicated_count": 0,
            "total_duration_ns": 1_000_000,
            "blocking_issues": (),
            "warnings": (),
        },
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
    }
    base.update(overrides)
    return VersionFacts(**base)  # type: ignore[arg-type]


def _snapshot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "target_episode_count": 10,
        "target_duration_ns": 1_000_000,
        "scene_targets": ["kitchen"],
        "action_targets": ["pick"],
        "data_modalities": ["RGB"],
        "quality_thresholds": {"min_selected_count": 10},
        "required_schema_versions": [{"schema_id": "schema-1", "schema_version": 1}],
        "split_requirements": {
            "train_ratio": 0.8,
            "validation_ratio": 0.1,
            "test_ratio": 0.1,
        },
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
    }
    base.update(overrides)
    return base


# ---- 全维度正例 ----
def test_all_dimensions_pass_on_matching_facts() -> None:
    report = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY)
    assert report.status is MatchStatus.PASSED
    assert report.failed == ()
    assert report.unverifiable == ()
    assert report.blocked is False
    assert [item.dimension for item in report.results] == [
        MatchDimension.TARGET_EPISODE_COUNT,
        MatchDimension.TARGET_DURATION_NS,
        MatchDimension.SCENE_TARGETS,
        MatchDimension.ACTION_TARGETS,
        MatchDimension.DATA_MODALITIES,
        MatchDimension.QUALITY_THRESHOLDS,
        MatchDimension.REQUIRED_SCHEMA_VERSIONS,
        MatchDimension.SPLIT_REQUIREMENTS,
        MatchDimension.FORMAT,
    ]


# ---- 样本数 ----
def test_episode_count_passes_when_selected_exceeds_target() -> None:
    report = evaluate_requirement_match(_snapshot(target_episode_count=5), _full_facts(), POLICY)
    item = _result_by_dimension(report, MatchDimension.TARGET_EPISODE_COUNT)
    assert item.status is MatchStatus.PASSED
    assert item.expected == 5
    assert item.actual == 10


def test_episode_count_fails_when_selected_short() -> None:
    report = evaluate_requirement_match(_snapshot(target_episode_count=11), _full_facts(), POLICY)
    item = _result_by_dimension(report, MatchDimension.TARGET_EPISODE_COUNT)
    assert item.status is MatchStatus.FAILED
    assert report.status is MatchStatus.FAILED
    assert report.blocked is True


def test_episode_count_unverifiable_without_selected_count() -> None:
    facts = _full_facts(selected_count=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.TARGET_EPISODE_COUNT)
    assert item.status is MatchStatus.UNVERIFIABLE
    # 关键断言：无法核对不得被当成通过，也不得算作失败。
    assert item.status is not MatchStatus.PASSED
    assert item not in report.failed
    assert item in report.unverifiable


# ---- 时长 ----
def test_duration_fails_when_total_short() -> None:
    facts = _full_facts(total_duration_ns=999_999)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.TARGET_DURATION_NS)
    assert item.status is MatchStatus.FAILED


def test_duration_unverifiable_without_total() -> None:
    facts = _full_facts(total_duration_ns=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.TARGET_DURATION_NS).status is (
        MatchStatus.UNVERIFIABLE
    )


def test_duration_passes_when_total_sufficient() -> None:
    facts = _full_facts(total_duration_ns=2_000_000)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.TARGET_DURATION_NS).status is (
        MatchStatus.PASSED
    )


# ---- 场景 ----
def test_scene_targets_fail_when_label_missing() -> None:
    facts = _full_facts(scene_labels=frozenset({"warehouse"}))
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.SCENE_TARGETS)
    assert item.status is MatchStatus.FAILED
    assert "kitchen" in item.detail


def test_scene_targets_unverifiable_when_not_projected() -> None:
    facts = _full_facts(scene_labels=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.SCENE_TARGETS).status is (
        MatchStatus.UNVERIFIABLE
    )


# ---- 动作 ----
def test_action_targets_pass_against_label_key_or_value() -> None:
    report = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY)
    item = _result_by_dimension(report, MatchDimension.ACTION_TARGETS)
    assert item.status is MatchStatus.PASSED
    # 近似核对必须在 detail 里说清楚，避免被读成语义等价校验。
    assert "近似" in item.detail


def test_action_targets_fail_when_absent() -> None:
    facts = _full_facts(annotation_labels={"verb": frozenset({"push"})})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.ACTION_TARGETS).status is (
        MatchStatus.FAILED
    )


def test_action_targets_unverifiable_when_labels_empty() -> None:
    """版本内一个标签都没有 = 信息缺失，不是"核对不通过"。"""
    facts = _full_facts(annotation_labels={})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.ACTION_TARGETS).status is (
        MatchStatus.UNVERIFIABLE
    )


# ---- 模态 ----
def test_modalities_fail_when_missing() -> None:
    facts = _full_facts(data_modalities=frozenset({"DEPTH"}))
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.DATA_MODALITIES).status is (
        MatchStatus.FAILED
    )


def test_modalities_unverifiable_without_declaration() -> None:
    facts = _full_facts(data_modalities=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.DATA_MODALITIES)
    assert item.status is MatchStatus.UNVERIFIABLE
    assert item in report.unverifiable


# ---- 质量阈值 ----
def test_quality_threshold_fails_when_metric_below() -> None:
    snapshot = _snapshot(quality_thresholds={"min_selected_count": 20})
    report = evaluate_requirement_match(snapshot, _full_facts(), POLICY)
    item = _result_by_dimension(report, MatchDimension.QUALITY_THRESHOLDS)
    assert item.status is MatchStatus.FAILED
    assert item.actual["min_selected_count"]["status"] == "FAILED"


def test_quality_threshold_unknown_key_is_unverifiable_not_pass() -> None:
    """自定义阈值键没有对应事实 —— 必须 UNVERIFIABLE，不能默认通过。"""
    snapshot = _snapshot(quality_thresholds={"min_quality_score": 0.9})
    report = evaluate_requirement_match(snapshot, _full_facts(), POLICY)
    item = _result_by_dimension(report, MatchDimension.QUALITY_THRESHOLDS)
    assert item.status is MatchStatus.UNVERIFIABLE
    assert "min_quality_score" in item.detail


def test_quality_threshold_unverifiable_without_report() -> None:
    facts = _full_facts(quality_metrics=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.QUALITY_THRESHOLDS).status is (
        MatchStatus.UNVERIFIABLE
    )


def test_quality_threshold_blocking_issue_count_fails() -> None:
    facts = _full_facts(
        quality_metrics={"selected_count": 10, "blocking_issues": ("DATASET_EMPTY",)}
    )
    snapshot = _snapshot(quality_thresholds={"max_blocking_issue_count": 0})
    report = evaluate_requirement_match(snapshot, facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.QUALITY_THRESHOLDS).status is (
        MatchStatus.FAILED
    )


# ---- Schema 版本 ----
def test_schema_version_missing_in_version_fails() -> None:
    facts = _full_facts(annotation_schema_versions=frozenset({("schema-2", 1)}))
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.REQUIRED_SCHEMA_VERSIONS).status is (
        MatchStatus.FAILED
    )


def test_schema_version_unverifiable_without_projection() -> None:
    facts = _full_facts(annotation_schema_versions=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.REQUIRED_SCHEMA_VERSIONS).status is (
        MatchStatus.UNVERIFIABLE
    )


def test_schema_version_passes_on_exact_match() -> None:
    report = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY)
    assert _result_by_dimension(report, MatchDimension.REQUIRED_SCHEMA_VERSIONS).status is (
        MatchStatus.PASSED
    )


# ---- 划分 ----
def test_split_ratio_outside_tolerance_fails() -> None:
    facts = _full_facts(split_counts={"train": 5, "validation": 3, "test": 2})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.SPLIT_REQUIREMENTS)
    assert item.status is MatchStatus.FAILED
    assert "train_ratio" in item.detail


def test_split_ratio_within_tolerance_passes() -> None:
    # 7/10 = 0.7，与 0.8 相差 0.1 > 0.02 → 失败；6/8 这种整数舍入才该通过。
    facts = _full_facts(split_counts={"train": 9, "validation": 1, "test": 0})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.SPLIT_REQUIREMENTS)
    assert item.status is MatchStatus.FAILED
    loose = MatchPolicy(
        on_failed=EnforcementPolicy.BLOCK,
        on_unverifiable=EnforcementPolicy.WARN,
        split_ratio_tolerance=0.15,
    )
    relaxed = evaluate_requirement_match(_snapshot(), facts, loose)
    assert _result_by_dimension(relaxed, MatchDimension.SPLIT_REQUIREMENTS).status is (
        MatchStatus.PASSED
    )


def test_split_counts_key_mapping_is_explicit() -> None:
    """需求 ``train_ratio`` 与版本 ``train`` 键名不同，必须经显式映射核对。

    这是真实的回归点：直接拿需求键查 ``split_counts`` 会恒取到 0，
    把「划分完全正确」误判成「完全不符」。
    """
    facts = _full_facts(split_counts={"train": 80, "validation": 10, "test": 10})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    item = _result_by_dimension(report, MatchDimension.SPLIT_REQUIREMENTS)
    assert item.status is MatchStatus.PASSED
    assert item.actual["train_ratio"]["actual"] == pytest.approx(0.8)


def test_split_unverifiable_when_counts_missing() -> None:
    facts = _full_facts(split_counts=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.SPLIT_REQUIREMENTS).status is (
        MatchStatus.UNVERIFIABLE
    )


def test_split_empty_version_is_unverifiable() -> None:
    facts = _full_facts(split_counts={"train": 0, "validation": 0, "test": 0})
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.SPLIT_REQUIREMENTS).status is (
        MatchStatus.UNVERIFIABLE
    )


# ---- 格式 ----
def test_format_mismatch_fails() -> None:
    facts = _full_facts(format_version="ddp.lerobot/0.9.0")
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.FORMAT).status is MatchStatus.FAILED


def test_format_unverifiable_without_declaration() -> None:
    facts = _full_facts(format=None, format_version=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert _result_by_dimension(report, MatchDimension.FORMAT).status is (MatchStatus.UNVERIFIABLE)


# ---- 策略 ----
def test_unverifiable_policy_block_makes_report_blocked() -> None:
    strict = MatchPolicy(
        on_failed=EnforcementPolicy.BLOCK,
        on_unverifiable=EnforcementPolicy.BLOCK,
        split_ratio_tolerance=0.02,
    )
    facts = _full_facts(data_modalities=None)
    report = evaluate_requirement_match(_snapshot(), facts, strict)
    assert report.status is MatchStatus.UNVERIFIABLE
    assert report.failed == ()
    assert report.blocked is True
    assert [item.dimension for item in report.blocking_dimensions] == [
        MatchDimension.DATA_MODALITIES
    ]


def test_unverifiable_policy_warn_does_not_block() -> None:
    facts = _full_facts(data_modalities=None)
    report = evaluate_requirement_match(_snapshot(), facts, POLICY)
    assert report.blocked is False
    assert report.status is MatchStatus.UNVERIFIABLE


def test_failed_policy_warn_does_not_block_but_keeps_failed_status() -> None:
    lenient = MatchPolicy(
        on_failed=EnforcementPolicy.WARN,
        on_unverifiable=EnforcementPolicy.WARN,
        split_ratio_tolerance=0.02,
    )
    report = evaluate_requirement_match(_snapshot(target_episode_count=99), _full_facts(), lenient)
    assert report.status is MatchStatus.FAILED
    assert report.blocked is False


def test_invalid_policy_values_are_rejected() -> None:
    from app.domain.datasets.errors import DomainValidationError

    with pytest.raises(DomainValidationError):
        MatchPolicy(on_failed="IGNORE", on_unverifiable="WARN", split_ratio_tolerance=0.01)
    with pytest.raises(DomainValidationError):
        MatchPolicy(on_failed="BLOCK", on_unverifiable="WARN", split_ratio_tolerance=1.5)


# ---- 确定性与健壮性 ----
def test_report_is_deterministic() -> None:
    first = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY).to_payload()
    second = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY).to_payload()
    assert first == second
    # 载荷必须可 JSON 序列化（落库用）。
    import json

    assert json.loads(json.dumps(first, ensure_ascii=False)) == first


def test_empty_snapshot_does_not_crash() -> None:
    report = evaluate_requirement_match({}, VersionFacts(), POLICY)
    assert report.status is MatchStatus.PASSED
    assert len(report.results) == 9


def test_non_mapping_snapshot_does_not_crash() -> None:
    report = evaluate_requirement_match(None, VersionFacts(), POLICY)
    assert len(report.results) == 9


def test_malformed_snapshot_values_are_unverifiable() -> None:
    snapshot = _snapshot(
        target_episode_count="ten",
        target_duration_ns=[1, 2],
        scene_targets="kitchen",
        action_targets={"pick": True},
        quality_thresholds="all-good",
        required_schema_versions="v1",
        split_requirements=["0.8"],
    )
    report = evaluate_requirement_match(snapshot, _full_facts(), POLICY)
    by_name = {item.dimension: item for item in report.results}
    assert by_name[MatchDimension.TARGET_EPISODE_COUNT].status is MatchStatus.UNVERIFIABLE
    assert by_name[MatchDimension.TARGET_DURATION_NS].status is MatchStatus.UNVERIFIABLE
    # 非列表的目标集合按"未指定"处理（快照已由领域层规范化，这里只求不崩）。
    assert by_name[MatchDimension.SCENE_TARGETS].status is MatchStatus.PASSED
    assert by_name[MatchDimension.ACTION_TARGETS].status is MatchStatus.PASSED
    assert by_name[MatchDimension.QUALITY_THRESHOLDS].status is MatchStatus.PASSED
    assert by_name[MatchDimension.REQUIRED_SCHEMA_VERSIONS].status is MatchStatus.UNVERIFIABLE
    assert by_name[MatchDimension.SPLIT_REQUIREMENTS].status is MatchStatus.PASSED


def test_payload_status_values_are_readable_enum_names() -> None:
    payload = evaluate_requirement_match(_snapshot(), _full_facts(), POLICY).to_payload()
    assert payload["status"] == "PASSED"
    assert payload["policy"]["on_failed"] == "BLOCK"
    assert payload["failed_dimensions"] == []
    assert {item["status"] for item in payload["dimensions"]} == {"PASSED"}
