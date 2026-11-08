"""数据集领域单测：选择、去重、划分、泄漏、状态机与 Manifest。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.datasets.dedup import deduplicate
from app.domain.datasets.enums import (
    DatasetVersionStatus,
    DedupKey,
    EmptySplitPolicy,
    GroupingKey,
    LeakageRule,
    LeakageStatus,
    RetainRule,
    RoundingRule,
    Split,
    SplitStrategy,
    ValidityStatus,
)
from app.domain.datasets.errors import (
    InvalidStateTransitionError,
    LeakageWaiverForbiddenError,
)
from app.domain.datasets.leakage import LeakageItem, LeakageWaiver, check_leakage
from app.domain.datasets.manifest import build_manifest
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.policies import DedupPolicy, LeakagePolicy, PolicyRef, SplitPolicy
from app.domain.datasets.selection import (
    EpisodeCandidate,
    SelectionFilters,
    evaluate_selection,
)
from app.domain.datasets.split import SplitStatus, assign_splits
from app.domain.datasets.state_machines import validity_target, version_target

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def candidate(
    index: int,
    *,
    lineage: UUID | None = None,
    manifest: str | None = None,
    raw_hash: str | None = None,
    scene: str = "kitchen",
    status: str = "ACCEPTED",
    quality: str = "PASS",
    annotation_status: str = "APPROVED",
    time_sync: str = "VERIFIED",
    calibration_valid: bool = True,
) -> EpisodeCandidate:
    return EpisodeCandidate(
        episode_id=UUID(int=index + 1),
        episode_lineage_id=lineage or UUID(int=1000 + index),
        episode_version=1,
        code=f"ep-{index}",
        status=status,
        quality_status=quality,
        annotation_status=annotation_status,
        manifest_sha256=manifest or f"sha256:{(index % 10) * 'a' + 'b' * 64}"[:71],
        scene_labels=(scene,),
        robot_id=None,
        device_ids=(),
        batch_id=UUID(int=5000 + index // 2),
        task_id=None,
        start_time_ns=index * 10_000,
        end_time_ns=index * 10_000 + 5_000,
        duration_ns=5_000,
        captured_at=NOW,
        updated_at=NOW,
        time_sync_status=time_sync,
        calibration_valid=calibration_valid,
        calibration_issue=None if calibration_valid else "缺少标定",
        annotation_submission_id=UUID(int=9000),
        annotation_version=1,
        annotation_record_digest=SHA,
        annotation_submission_status="APPROVED",
        schema_id=UUID(int=9100),
        schema_version=1,
        schema_sha256=SHA_B,
        raw_asset_ids=(UUID(int=2000 + index),),
        raw_asset_hashes=(raw_hash or f"sha256:{(index % 10) * 'c' + 'd' * 64}"[:71],),
        raw_asset_statuses=("VALID",),
        total_bytes=1024,
    )


def dedup_policy(**overrides) -> DedupPolicy:
    values = {
        "dedup_policy_id": uuid4(),
        "project_id": uuid4(),
        "dataset_id": uuid4(),
        "version": 1,
        "exact_keys": (
            DedupKey.EPISODE_LINEAGE,
            DedupKey.MANIFEST_SHA256,
            DedupKey.RAW_ASSET_SHA256,
        ),
        "retain_rule": RetainRule.NEWEST_ACCEPTED,
    }
    values.update(overrides)
    return DedupPolicy(**values)


def split_policy(**overrides) -> SplitPolicy:
    values = {
        "split_policy_id": uuid4(),
        "project_id": uuid4(),
        "dataset_id": uuid4(),
        "version": 1,
        "strategy": SplitStrategy.RATIO,
        "seed": 42,
        "group_by": (GroupingKey.EPISODE_LINEAGE,),
        "empty_split_policy": EmptySplitPolicy.FAIL,
        "min_item_count": 1,
        "min_duration_ns": 0,
        "rounding_rule": RoundingRule.LARGEST_REMAINDER,
    }
    values.update(overrides)
    return SplitPolicy(**values)


def leakage_policy(**overrides) -> LeakagePolicy:
    values = {
        "leakage_policy_id": uuid4(),
        "project_id": uuid4(),
        "dataset_id": uuid4(),
        "version": 1,
        "hard_rules": (
            LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,
            LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,
            LeakageRule.RAW_ASSET_SHA256_CROSS_SPLIT,
        ),
        "soft_rules": (LeakageRule.SCENE_GROUP_CROSS_SPLIT,),
        "waiver_allowed_rules": (LeakageRule.SCENE_GROUP_CROSS_SPLIT,),
        "scene_grouping_enabled": True,
        "temporal_overlap_enabled": False,
    }
    values.update(overrides)
    return LeakagePolicy(**values)


def test_selection_eligibility_and_exclusion_summary() -> None:
    candidates = (
        candidate(0),
        candidate(1, status="INVALIDATED"),
        candidate(2, quality="FAIL"),
        candidate(3, time_sync="UNVERIFIED"),
        candidate(4, calibration_valid=False),
        candidate(5, annotation_status="SUBMITTED"),
    )
    outcome = evaluate_selection(
        candidates,
        filters=SelectionFilters(),
        usage_policy="TRAINING_ALLOWED",
    )
    assert outcome.candidate_count == 6
    assert [item.episode_id for item in outcome.selected] == [candidates[0].episode_id]
    codes = {item["code"] for item in outcome.exclusion_summary}
    assert "EPISODE_STATUS_INELIGIBLE" in codes
    assert "QUALITY_NOT_PASSED" in codes
    assert "TIME_SYNC_UNKNOWN" in codes
    assert "CALIBRATION_INVALID" in codes
    assert "ANNOTATION_NOT_APPROVED" in codes
    assert outcome.excluded_count == 5


def test_warn_requires_confirmation() -> None:
    warn = candidate(0, quality="WARN")
    denied = evaluate_selection(
        (warn,), filters=SelectionFilters(), usage_policy="TRAINING_ALLOWED"
    )
    assert denied.excluded_count == 1
    allowed = evaluate_selection(
        (warn,),
        filters=SelectionFilters.from_mapping(
            {"allow_warn": True, "warn_confirmation_reason": "QA 已确认"}
        ),
        usage_policy="TRAINING_ALLOWED",
    )
    assert allowed.selected_count == 1
    assert "QUALITY_WARN_CONFIRMED:" + str(warn.episode_id) in allowed.warnings


def test_usage_policy_blocks_without_waiver() -> None:
    blocked = evaluate_selection(
        (candidate(0),), filters=SelectionFilters(), usage_policy="TRAINING_DENIED"
    )
    assert blocked.selected_count == 0
    assert blocked.exclusion_summary[0]["code"] == "USAGE_POLICY_DENIED"
    waived = evaluate_selection(
        (candidate(0),),
        filters=SelectionFilters.from_mapping(
            {"usage_policy_waiver_reason": "PO 书面批准"}
        ),
        usage_policy="TRAINING_DENIED",
    )
    assert waived.selected_count == 1
    assert "USAGE_POLICY_WAIVED" in waived.warnings


def test_exact_dedup_is_deterministic_and_traced() -> None:
    lineage = uuid4()
    manifest = SHA
    raw = SHA_B
    candidates = (
        candidate(0, lineage=lineage),
        candidate(1, lineage=lineage),
        candidate(2, manifest=manifest),
        candidate(3, manifest=manifest),
        candidate(4, raw_hash=raw),
        candidate(5, raw_hash=raw),
        candidate(6),
    )
    first = deduplicate(candidates, dedup_policy())
    second = deduplicate(candidates, dedup_policy())
    assert first.duplicated_count == 3
    assert [item.episode_id for item in first.selected] == [
        item.episode_id for item in second.selected
    ]
    assert {item.group_id for item in first.groups} == {
        item.group_id for item in second.groups
    }
    codes = {item.code for item in first.excluded}
    assert codes == {
        "DUPLICATE_EPISODE_LINEAGE",
        "DUPLICATE_MANIFEST_SHA256",
        "DUPLICATE_RAW_ASSET_SHA256",
    }


def test_split_is_deterministic_and_group_aware() -> None:
    lineage = uuid4()
    candidates = tuple(candidate(i, lineage=lineage if i < 2 else None) for i in range(10))
    policy = split_policy()
    first = assign_splits(candidates, policy)
    second = assign_splits(candidates, policy)
    assert first.status is SplitStatus.OK
    assert first.assignments == second.assignments
    assert first.units == second.units
    splits_for_lineage = {first.assignments[candidates[i].episode_id] for i in (0, 1)}
    assert len(splits_for_lineage) == 1
    assert first.train_count + first.validation_count + first.test_count == 9


def test_split_seed_changes_assignment_but_not_counts() -> None:
    candidates = tuple(candidate(i) for i in range(10))
    first = assign_splits(candidates, split_policy(seed=1))
    second = assign_splits(candidates, split_policy(seed=2))
    assert first.counts == second.counts
    assert first.assignments != second.assignments


def test_empty_test_split_fails_by_default() -> None:
    candidates = tuple(candidate(i) for i in range(4))
    outcome = assign_splits(candidates, split_policy())
    assert outcome.status is SplitStatus.EMPTY_TEST_SPLIT
    allowed = assign_splits(
        candidates,
        split_policy(empty_split_policy=EmptySplitPolicy.ALLOW_EMPTY_TEST),
    )
    assert allowed.status is SplitStatus.OK
    assert "EMPTY_TEST_SPLIT_ALLOWED" in allowed.warnings


def _leakage_items(assignments: dict[int, Split]) -> tuple[LeakageItem, ...]:
    lineage = uuid4()
    result = []
    for index, split in assignments.items():
        result.append(
            LeakageItem(
                episode_id=UUID(int=index + 1),
                episode_lineage_id=lineage if index < 2 else uuid4(),
                manifest_sha256=f"sha256:{index:064x}"[:71],
                raw_asset_ids=(UUID(int=2000 + index),),
                raw_asset_hashes=(f"sha256:{index:064x}"[:71],),
                split=split,
                annotation_submission_id=UUID(int=9000 + index),
                annotation_record_digest=SHA,
                scene_group_id=UUID(int=7000),
                batch_id=None,
                start_time_ns=index * 1000,
                end_time_ns=index * 1000 + 500,
            )
        )
    return tuple(result)


def test_hard_leakage_fails_and_cannot_be_waived() -> None:
    items = _leakage_items({0: Split.TRAIN, 1: Split.TEST})
    report = check_leakage(
        items,
        leakage_policy(),
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
        source_snapshot_at=NOW,
    )
    assert report.leakage_status is LeakageStatus.FAIL
    assert report.hard_violation_count == 1
    waiver = LeakageWaiver(
        rule=LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,
        approved_by=uuid4(),
        reason="尝试豁免",
        approved_at=NOW,
        audit_ref="audit-1",
    )
    with pytest.raises(LeakageWaiverForbiddenError):
        check_leakage(
            items,
            leakage_policy(hard_rules=(LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,)),
            leakage_report_id=uuid4(),
            project_id=uuid4(),
            dataset_version_id=uuid4(),
            checked_at=NOW,
            waivers=(
                LeakageWaiver(
                    rule=LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,
                    approved_by=uuid4(),
                    reason="尝试豁免硬规则",
                    approved_at=NOW,
                    audit_ref="audit-hard",
                ),
            ),
        )


def test_soft_leakage_warn_then_waived() -> None:
    items = _leakage_items({0: Split.TRAIN, 1: Split.TEST})
    policy = leakage_policy(hard_rules=(LeakageRule.MANIFEST_SHA256_CROSS_SPLIT,))
    report = check_leakage(
        items,
        policy,
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
    )
    assert report.leakage_status is LeakageStatus.WARN
    waiver = LeakageWaiver(
        rule=LeakageRule.SCENE_GROUP_CROSS_SPLIT,
        approved_by=uuid4(),
        reason="同场景但不同采集批次",
        approved_at=NOW,
        audit_ref="audit-2",
    )
    waived = check_leakage(
        items,
        policy,
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
        waivers=(waiver,),
    )
    assert waived.leakage_status is LeakageStatus.WAIVED


def test_leakage_unknown_when_scene_group_missing() -> None:
    item = LeakageItem(
        episode_id=uuid4(),
        episode_lineage_id=uuid4(),
        manifest_sha256=SHA,
        raw_asset_ids=(uuid4(),),
        raw_asset_hashes=(SHA_B,),
        split=Split.TRAIN,
        annotation_submission_id=uuid4(),
        annotation_record_digest=SHA,
        scene_group_id=None,
        batch_id=None,
        start_time_ns=0,
        end_time_ns=10,
    )
    report = check_leakage(
        (item,),
        leakage_policy(),
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
    )
    assert report.leakage_status is LeakageStatus.UNKNOWN
    assert report.unknown_reason == "SCENE_GROUP_MISSING"


def test_state_machines_and_validity_separation() -> None:
    assert version_target(DatasetVersionStatus.READY, "freeze") is DatasetVersionStatus.FROZEN
    with pytest.raises(InvalidStateTransitionError):
        version_target(DatasetVersionStatus.FROZEN, "build")
    assert validity_target(ValidityStatus.VALID, "system-mark-stale") is ValidityStatus.STALE
    assert validity_target(ValidityStatus.STALE, "invalidate") is ValidityStatus.INVALIDATED
    with pytest.raises(InvalidStateTransitionError):
        validity_target(ValidityStatus.INVALIDATED, "revalidate")


def test_manifest_hash_is_deterministic_and_detects_change() -> None:
    project_id, dataset_id, version_id, lineage_id, actor = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
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
        train_count=1,
        validation_count=0,
        test_count=0,
        item_count=1,
        source_snapshot_at=NOW,
        created_by=actor,
    )
    item = DatasetItem(
        dataset_item_id=uuid4(),
        project_id=project_id,
        dataset_version_id=version_id,
        episode_id=uuid4(),
        episode_version=1,
        episode_lineage_id=lineage_id,
        manifest_sha256=SHA,
        raw_asset_ids=(uuid4(),),
        annotation_submission_id=uuid4(),
        annotation_version=1,
        annotation_record_digest=SHA,
        schema_id=uuid4(),
        schema_version=1,
        schema_sha256=SHA_B,
        split=Split.TRAIN,
        created_at=NOW,
    )
    temporary = replace(
        version,
        quality_report_sha256=SHA,
        leakage_report_sha256=SHA_B,
        episode_version_digest=SHA,
    )
    first = build_manifest(
        temporary,
        (item,),
        episode_version_digest=SHA,
        annotation_submission_refs_digest=None,
        annotation_schema_versions=(),
        created_at=NOW,
    )
    second = build_manifest(
        temporary,
        (item,),
        episode_version_digest=SHA,
        annotation_submission_refs_digest=None,
        annotation_schema_versions=(),
        created_at=NOW,
    )
    assert first.manifest_sha256 == second.manifest_sha256
    altered = replace(item, selected_reason="CHANGED", item_sha256=None)
    changed = build_manifest(
        temporary,
        (altered,),
        episode_version_digest=SHA,
        annotation_submission_refs_digest=None,
        annotation_schema_versions=(),
        created_at=NOW,
    )
    assert changed.manifest_sha256 != first.manifest_sha256
