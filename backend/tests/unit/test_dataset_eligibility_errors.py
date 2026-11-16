"""数据集资格类错误码的产生点单测。

覆盖四个此前全仓零产生点的码：
- ``dataset_item_ineligible``
- ``dataset_annotation_not_approved``
- ``dataset_annotation_snapshot_mismatch``
- ``dataset_access_denied``（判定逻辑在应用层，见 test_dataset_grant_api.py）

判据来自契约原文，不是"看起来该有"：
- 阶段 4 ``error-codes.v1.json``：item_ineligible 描述为
  「Episode/标注/Manifest/标定/时间同步/用途不合格」；
- AC-DS-29：「任务冻结 Schema/标注版本；DatasetItem 引用不同 Schema 版本或
  不存在的提交」→「快照不一致拒绝」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.datasets.enums import (
    DedupKey,
    EmptySplitPolicy,
    ExclusionCode,
    GroupingKey,
    LeakageRule,
    RetainRule,
    RoundingRule,
    SplitStrategy,
)
from app.domain.datasets.errors import (
    AnnotationNotApprovedError,
    AnnotationSnapshotMismatchError,
    ItemIneligibleError,
)
from app.domain.datasets.pipeline import run_pipeline
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    PolicyRef,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.selection import (
    EpisodeCandidate,
    SelectionFilters,
    contract_code_for_exclusion,
    evaluate_selection,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _candidate(
    index: int,
    *,
    annotation_status: str = "APPROVED",
    submission_status: str = "APPROVED",
    quality_status: str = "PASS",
    time_sync_status: str = "VERIFIED",
    record_schema_version: int = 1,
    submission_schema_version: int = 1,
) -> EpisodeCandidate:
    return EpisodeCandidate(
        episode_id=UUID(int=index + 1),
        episode_lineage_id=UUID(int=1000 + index),
        episode_version=1,
        code=f"ep-{index}",
        status="ACCEPTED",
        quality_status=quality_status,
        annotation_status=annotation_status,
        manifest_sha256=f"sha256:{index:064x}",
        scene_labels=("kitchen",),
        robot_id=None,
        device_ids=(),
        batch_id=None,
        task_id=None,
        start_time_ns=index * 10_000,
        end_time_ns=index * 10_000 + 5_000,
        duration_ns=5_000,
        captured_at=NOW,
        updated_at=NOW,
        time_sync_status=time_sync_status,
        calibration_valid=True,
        calibration_issue=None,
        annotation_submission_id=UUID(int=9000),
        annotation_version=1,
        annotation_record_digest=SHA,
        annotation_submission_status=submission_status,
        schema_id=UUID(int=9100),
        schema_version=record_schema_version,
        schema_sha256=SHA_B,
        raw_asset_ids=(UUID(int=2000 + index),),
        raw_asset_hashes=(f"sha256:{index + 50:064x}",),
        raw_asset_statuses=("VALID",),
        submission_schema_id=UUID(int=9100),
        submission_schema_version=submission_schema_version,
        total_bytes=1024,
    )


def _pipeline_kwargs(candidates: tuple[EpisodeCandidate, ...], filters: SelectionFilters):
    project_id, dataset_id, version_id, lineage_id = uuid4(), uuid4(), uuid4(), uuid4()
    from app.domain.datasets.models import DatasetVersion

    version = DatasetVersion(
        dataset_version_id=version_id,
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_lineage_id=lineage_id,
        dataset_version=1,
        selection_rule_version="sel/1",
        selection_filters={},
        selection_snapshot_at=NOW,
        dedup_policy_ref=PolicyRef(uuid4(), 1, SHA),
        split_policy_ref=PolicyRef(uuid4(), 1, SHA),
        leakage_policy_ref=PolicyRef(uuid4(), 1, SHA),
        quality_policy_ref=PolicyRef(uuid4(), 1, SHA),
        source_snapshot_at=NOW,
        created_by=uuid4(),
    )
    return {
        "base_version": version,
        "candidates": candidates,
        "filters": filters,
        "usage_policy": "TRAINING_ALLOWED",
        "dedup_policy": DedupPolicy(
            dedup_policy_id=uuid4(),
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            exact_keys=(
                DedupKey.EPISODE_LINEAGE,
                DedupKey.MANIFEST_SHA256,
                DedupKey.RAW_ASSET_SHA256,
            ),
            retain_rule=RetainRule.NEWEST_ACCEPTED,
        ),
        "split_policy": SplitPolicy(
            split_policy_id=uuid4(),
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            strategy=SplitStrategy.RATIO,
            seed=42,
            group_by=(GroupingKey.EPISODE_LINEAGE,),
            empty_split_policy=EmptySplitPolicy.ALLOW_EMPTY_TEST,
            min_item_count=1,
            min_duration_ns=0,
            rounding_rule=RoundingRule.LARGEST_REMAINDER,
        ),
        "leakage_policy": LeakagePolicy(
            leakage_policy_id=uuid4(),
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            hard_rules=(LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,),
            soft_rules=(),
            waiver_allowed_rules=(),
            scene_grouping_enabled=False,
            temporal_overlap_enabled=False,
        ),
        "quality_policy": QualityPolicy(
            quality_policy_id=uuid4(),
            project_id=project_id,
            dataset_id=dataset_id,
            version=1,
            min_selected_count=1,
        ),
        "leakage_report_id": uuid4(),
        "quality_report_id": uuid4(),
        "now": NOW,
        "source_snapshot_at": NOW,
    }


def test_exclusion_codes_map_to_distinct_contract_codes() -> None:
    """排除码归并必须可区分；去重类不是资格错误，返回 None。"""
    assert (
        contract_code_for_exclusion(ExclusionCode.ANNOTATION_NOT_APPROVED)
        == "dataset_annotation_not_approved"
    )
    assert (
        contract_code_for_exclusion(ExclusionCode.ANNOTATION_SNAPSHOT_MISMATCH)
        == "dataset_annotation_snapshot_mismatch"
    )
    assert (
        contract_code_for_exclusion(ExclusionCode.CALIBRATION_INVALID) == "dataset_item_ineligible"
    )
    # 去重是正常结果，不能被当成"不合格"报错。
    assert contract_code_for_exclusion(ExclusionCode.DUPLICATE_MANIFEST_SHA256) is None


def test_snapshot_mismatch_is_excluded_with_own_code() -> None:
    """AC-DS-29：标注记录 Schema 与提交冻结快照不一致必须单独成码。"""
    candidates = (_candidate(0, record_schema_version=1, submission_schema_version=2),)
    outcome = evaluate_selection(
        candidates,
        filters=SelectionFilters(),
        usage_policy="TRAINING_ALLOWED",
    )
    assert outcome.selected == ()
    assert [item.code for item in outcome.excluded] == [ExclusionCode.ANNOTATION_SNAPSHOT_MISMATCH]
    assert (
        contract_code_for_exclusion(outcome.excluded[0].code)
        == "dataset_annotation_snapshot_mismatch"
    )


def test_explicit_ineligible_selection_raises_item_ineligible() -> None:
    """显式点名的不合格 Episode 必须确定性拒绝，并带出被拒条目。"""
    candidates = (_candidate(0, quality_status="FAIL"),)
    filters = SelectionFilters(episode_ids=(UUID(int=1),))
    kwargs = _pipeline_kwargs(candidates, filters)

    with pytest.raises(ItemIneligibleError) as excinfo:
        run_pipeline(**kwargs, require_explicit_candidates=True)

    assert excinfo.value.code == "dataset_item_ineligible"
    assert excinfo.value.details["episode_ids"] == [str(UUID(int=1))]
    assert excinfo.value.details["requested_episode_ids"] == [str(UUID(int=1))]


def test_explicit_annotation_not_approved_is_reported_first() -> None:
    """引用未批准标注是前置输入错误，优先于泛化的不合格。"""
    candidates = (
        _candidate(0, annotation_status="REJECTED"),
        _candidate(1, submission_status="IN_REVIEW"),
    )
    filters = SelectionFilters(episode_ids=(UUID(int=1), UUID(int=2)))
    kwargs = _pipeline_kwargs(candidates, filters)

    with pytest.raises(AnnotationNotApprovedError) as excinfo:
        run_pipeline(**kwargs, require_explicit_candidates=True)

    assert excinfo.value.code == "dataset_annotation_not_approved"
    assert str(UUID(int=1)) in excinfo.value.details["episode_ids"]


def test_explicit_snapshot_mismatch_raises_snapshot_error() -> None:
    candidates = (_candidate(0, record_schema_version=1, submission_schema_version=3),)
    filters = SelectionFilters(episode_ids=(UUID(int=1),))
    kwargs = _pipeline_kwargs(candidates, filters)

    with pytest.raises(AnnotationSnapshotMismatchError) as excinfo:
        run_pipeline(**kwargs, require_explicit_candidates=True)

    assert excinfo.value.code == "dataset_annotation_snapshot_mismatch"
    assert excinfo.value.details["episode_ids"] == [str(UUID(int=1))]


def test_partial_selection_does_not_raise() -> None:
    """部分入选 + 部分排除是正常统计结果，不报错（只统计）。"""
    candidates = (
        _candidate(0),
        _candidate(1, annotation_status="REJECTED"),
    )
    filters = SelectionFilters(episode_ids=(UUID(int=1), UUID(int=2)))
    kwargs = _pipeline_kwargs(candidates, filters)

    result = run_pipeline(**kwargs, require_explicit_candidates=True)

    assert result.preview.selected_count == 1
    assert result.preview.excluded_count == 1


def test_unlisted_selection_stays_statistical() -> None:
    """未点名条目时"全排除"属数据不足，不走资格错误分支。"""
    candidates = (_candidate(0, annotation_status="REJECTED"),)
    filters = SelectionFilters()
    kwargs = _pipeline_kwargs(candidates, filters)

    result = run_pipeline(**kwargs, require_explicit_candidates=True)

    assert result.preview.selected_count == 0
    assert "DATASET_EMPTY" in result.blocking_issues or result.split_status.value != "OK"


# --------------------------------------------------------------------------
# 近重复检测接入 pipeline
# --------------------------------------------------------------------------


def _near_duplicate_kwargs(enforcement: str = "WARN_ONLY", enabled: bool = True):
    """构造一组时间区间完全重合、场景一致的候选，触发近重复检测。"""
    from dataclasses import replace as _replace

    # 同场景 + 完全重合的时间区间 = 元数据层面就是疑似重复。
    base = _candidate(0)
    twin = _replace(
        _candidate(1),
        start_time_ns=base.start_time_ns,
        end_time_ns=base.end_time_ns,
        duration_ns=base.duration_ns,
        scene_labels=base.scene_labels,
    )
    candidates = (base, twin)
    kwargs = _pipeline_kwargs(candidates, SelectionFilters())
    policy = kwargs["dedup_policy"]
    kwargs["dedup_policy"] = DedupPolicy(
        dedup_policy_id=policy.dedup_policy_id,
        project_id=policy.project_id,
        dataset_id=policy.dataset_id,
        version=policy.version,
        exact_keys=policy.exact_keys,
        retain_rule=policy.retain_rule,
        near_duplicate_enabled=enabled,
        near_duplicate_enforcement=enforcement,
        similarity_threshold=0.8,
        similarity_min_cluster_size=2,
    )
    return kwargs


def test_near_duplicate_warn_mode_records_finding_not_block() -> None:
    """WARN_ONLY 下近重复要留痕但不阻断构建。"""
    result = run_pipeline(**_near_duplicate_kwargs("WARN_ONLY"))
    assert any(item.startswith("NEAR_DUPLICATE_SUSPECTED") for item in result.warnings)
    assert not any("NEAR_DUPLICATE" in issue for issue in result.blocking_issues)
    summary = result.version_updates["exclusion_summary"]
    assert isinstance(summary, dict)
    assert "near_duplicate" in summary


def test_near_duplicate_block_mode_blocks() -> None:
    """BLOCK 下必须真的阻断——此前该模式只会产出"不支持"的阻断项。"""
    result = run_pipeline(**_near_duplicate_kwargs("BLOCK"))
    assert any(issue.startswith("NEAR_DUPLICATE_SUSPECTED") for issue in result.blocking_issues)
    assert not any("UNSUPPORTED" in issue for issue in result.blocking_issues)


def test_near_duplicate_disabled_leaves_no_summary() -> None:
    result = run_pipeline(**_near_duplicate_kwargs(enabled=False))
    summary = result.version_updates["exclusion_summary"]
    assert "near_duplicate" not in summary
