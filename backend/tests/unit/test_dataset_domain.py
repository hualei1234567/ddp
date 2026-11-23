"""数据集领域单测：选择、去重、划分、泄漏、状态机与 Manifest。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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
    DomainValidationError,
    InvalidStateTransitionError,
    LeakageWaiverForbiddenError,
)
from app.domain.datasets.leakage import (
    LeakageItem,
    LeakageReport,
    LeakageWaiver,
    check_leakage,
)
from app.domain.datasets.leakage import (
    _utc_iso as leakage_utc_iso,
)
from app.domain.datasets.manifest import _utc_iso as manifest_utc_iso
from app.domain.datasets.manifest import build_manifest
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.policies import DedupPolicy, LeakagePolicy, PolicyRef, SplitPolicy
from app.domain.datasets.quality import _utc_iso as quality_utc_iso
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
        filters=SelectionFilters.from_mapping({"usage_policy_waiver_reason": "PO 书面批准"}),
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
    assert {item.group_id for item in first.groups} == {item.group_id for item in second.groups}
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


def test_report_hashes_are_timezone_independent() -> None:
    """数据库会话时区不同不得改变报告 Hash（真实 PG 默认 Asia/Shanghai 曾暴露此缺陷）。"""
    shanghai = timezone(timedelta(hours=8))
    local = datetime(2026, 11, 9, 12, 0, tzinfo=shanghai)
    utc = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)
    assert leakage_utc_iso(local) == leakage_utc_iso(utc) == "2026-11-09T04:00:00+00:00"
    assert quality_utc_iso(local) == quality_utc_iso(utc)
    assert manifest_utc_iso(local) == manifest_utc_iso(utc)

    items = _leakage_items({0: Split.TRAIN, 1: Split.TEST})
    report = check_leakage(
        items,
        leakage_policy(),
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=local,
        source_snapshot_at=local,
    )
    rebuilt = LeakageReport(
        leakage_report_id=report.leakage_report_id,
        project_id=report.project_id,
        dataset_version_id=report.dataset_version_id,
        policy_ref=report.policy_ref,
        leakage_status=report.leakage_status,
        checked_rule_count=report.checked_rule_count,
        violations=report.violations,
        waivers=report.waivers,
        checked_at=utc,
        unknown_reason=report.unknown_reason,
        source_snapshot_at=utc,
        status=report.status,
        report_key=report.report_key,
        report_sha256=report.report_sha256,
        version=report.version,
    )
    assert rebuilt.report_sha256 == report.report_sha256


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
    # 来源不可达只能表达为 UNKNOWN；恢复后先回到 STALE，等人工 revalidate。
    assert validity_target(ValidityStatus.VALID, "system-mark-unknown") is ValidityStatus.UNKNOWN
    assert validity_target(ValidityStatus.UNKNOWN, "system-mark-stale") is ValidityStatus.STALE
    with pytest.raises(InvalidStateTransitionError):
        validity_target(ValidityStatus.UNKNOWN, "revalidate")
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


def test_selection_filters_accept_json_string_uuids() -> None:
    """HTTP JSON 传入的字符串 UUID 必须被解析，而不是 422（DDP-329 D2-1）。"""
    episode_id = uuid4()
    robot_id = uuid4()
    device_id = uuid4()
    filters = SelectionFilters.from_mapping(
        {
            "episode_ids": [str(episode_id)],
            "robot_ids": [str(robot_id)],
            "device_ids": [str(device_id)],
        }
    )
    assert filters.episode_ids == (episode_id,)
    assert filters.robot_ids == (robot_id,)
    assert filters.device_ids == (device_id,)


def test_selection_filters_reject_invalid_uuid_strings() -> None:
    with pytest.raises(DomainValidationError):
        SelectionFilters.from_mapping({"episode_ids": ["not-a-uuid"]})


def test_calibration_missing_is_distinguishable_from_invalid() -> None:
    """「没有标定」与「标定无效」必须是两个不同的排除码。

    此前 ``CALIBRATION_MISSING`` 是不可达死码：Episode 创建阶段就禁止空
    标定快照，空快照走不到选择阶段，两个码实际只可能出 ``CALIBRATION_INVALID``。
    调用方因此无法判断"该不该去补标定"，只能一律当成质量问题。
    """
    from dataclasses import replace as _replace

    missing = _replace(
        candidate(0, calibration_valid=False),
        calibration_missing=True,
        calibration_issue="缺少有效标定快照",
    )
    invalid = candidate(1, calibration_valid=False)

    outcome = evaluate_selection(
        (missing, invalid),
        filters=SelectionFilters(),
        usage_policy="TRAINING_ALLOWED",
    )
    by_id = {item["code"]: item for item in outcome.exclusion_summary}
    assert "CALIBRATION_MISSING" in by_id
    assert "CALIBRATION_INVALID" in by_id
    assert outcome.selected == ()


def test_missing_calibration_can_be_waived_with_reason() -> None:
    """缺少标定可带原因显式豁免；标定无效则不可豁免。

    公开第一人称数据集（HF Human-1M 等）的元数据里不含任何标定信息，
    这是数据固有属性。没有豁免时这类数据永远进不了数据集；但**有标定却
    无效/过期**是质量问题，风险完全不同，必须继续阻断。
    """
    from dataclasses import replace as _replace

    missing = _replace(
        candidate(0, calibration_valid=False),
        calibration_missing=True,
        calibration_issue="缺少有效标定快照",
    )
    waived = evaluate_selection(
        (missing,),
        filters=SelectionFilters.from_mapping(
            {
                "allow_missing_calibration": True,
                "missing_calibration_reason": "HF 公开数据集不含标定信息，人工确认接受",
            }
        ),
        usage_policy="TRAINING_ALLOWED",
    )
    assert len(waived.selected) == 1
    assert any(item.startswith("CALIBRATION_MISSING_WAIVED") for item in waived.warnings)


def test_waiver_does_not_cover_invalid_calibration() -> None:
    """豁免只覆盖 MISSING，不覆盖 INVALID。"""
    invalid = candidate(0, calibration_valid=False)
    outcome = evaluate_selection(
        (invalid,),
        filters=SelectionFilters.from_mapping(
            {
                "allow_missing_calibration": True,
                "missing_calibration_reason": "确认接受无标定",
            }
        ),
        usage_policy="TRAINING_ALLOWED",
    )
    assert outcome.selected == ()
    assert any(item["code"] == "CALIBRATION_INVALID" for item in outcome.exclusion_summary)


def test_waiver_requires_reason() -> None:
    with pytest.raises(DomainValidationError):
        SelectionFilters.from_mapping({"allow_missing_calibration": True})


# ---- V4.1 FR-030：采集会话 / 受试对象分组防泄漏 ----

_CAPTURE_SESSION = UUID(int=8801)
_SUBJECT = UUID(int=8802)


def _grouped_items(
    assignments: dict[int, Split],
    *,
    capture_session_id: UUID | None = None,
    subject_id: UUID | None = None,
) -> tuple[LeakageItem, ...]:
    """同分组维度的 Episode 列表：显式注入 session/subject，其余沿用硬规则可过的值。"""
    result = []
    for index, split in assignments.items():
        result.append(
            LeakageItem(
                episode_id=UUID(int=index + 1),
                # 血缘/内容 Hash 各不相同，确保只有被考察的分组规则能命中
                episode_lineage_id=uuid4(),
                manifest_sha256=f"sha256:{index:064x}"[:71],
                raw_asset_ids=(UUID(int=2000 + index),),
                raw_asset_hashes=(f"sha256:{index:064x}"[:71],),
                split=split,
                annotation_submission_id=UUID(int=9000 + index),
                annotation_record_digest=f"sha256:{index:064x}"[:71],
                scene_group_id=None,
                batch_id=None,
                capture_session_id=capture_session_id,
                subject_id=subject_id,
                start_time_ns=index * 10_000,
                end_time_ns=index * 10_000 + 500,
            )
        )
    return tuple(result)


def _check_with(items: tuple[LeakageItem, ...], rule: LeakageRule):
    # hard_rules 不允许为空（领域约束），这里保留一条不会命中的硬规则；
    # 被考察的分组规则一律进 soft_rules，断言只看它是否被检出。
    policy = leakage_policy(
        hard_rules=(LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,),
        soft_rules=(rule,),
        waiver_allowed_rules=(rule,),
        scene_grouping_enabled=False,
        temporal_overlap_enabled=False,
    )
    return check_leakage(
        items,
        policy,
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
        source_snapshot_at=NOW,
    )


def test_capture_session_cross_split_is_detected() -> None:
    """同一采集会话的 Episode 跨 train/test 必须被识别为泄漏。"""
    items = _grouped_items({0: Split.TRAIN, 1: Split.TEST}, capture_session_id=_CAPTURE_SESSION)
    report = _check_with(items, LeakageRule.CAPTURE_SESSION_CROSS_SPLIT)
    rules = {v.rule for v in report.violations}
    assert LeakageRule.CAPTURE_SESSION_CROSS_SPLIT in rules
    assert report.leakage_status in {LeakageStatus.WARN, LeakageStatus.FAIL}


def test_subject_cross_split_is_detected() -> None:
    """同一受试对象的 Episode 跨划分必须被识别。"""
    items = _grouped_items({0: Split.TRAIN, 1: Split.TEST}, subject_id=_SUBJECT)
    report = _check_with(items, LeakageRule.SUBJECT_CROSS_SPLIT)
    rules = {v.rule for v in report.violations}
    assert LeakageRule.SUBJECT_CROSS_SPLIT in rules


def test_same_group_single_split_is_clean() -> None:
    """整组落在同一划分时不得误报。"""
    items = _grouped_items(
        {0: Split.TRAIN, 1: Split.TRAIN},
        capture_session_id=_CAPTURE_SESSION,
        subject_id=_SUBJECT,
    )
    report = _check_with(items, LeakageRule.CAPTURE_SESSION_CROSS_SPLIT)
    assert report.violations == ()
    report = _check_with(items, LeakageRule.SUBJECT_CROSS_SPLIT)
    assert report.violations == ()


def test_missing_group_dimension_does_not_fabricate_violation() -> None:
    """维度缺失（None）表示「未知」，不得当作「同组」凭空造出违规。"""
    items = _grouped_items({0: Split.TRAIN, 1: Split.TEST})
    for rule in (
        LeakageRule.CAPTURE_SESSION_CROSS_SPLIT,
        LeakageRule.SUBJECT_CROSS_SPLIT,
    ):
        report = _check_with(items, rule)
        assert report.violations == ()


def test_grouping_switch_disables_rule() -> None:
    """策略关闭分组开关时，即使同组跨划分也不产出违规。"""
    items = _grouped_items({0: Split.TRAIN, 1: Split.TEST}, capture_session_id=_CAPTURE_SESSION)
    policy = leakage_policy(
        hard_rules=(LeakageRule.EPISODE_LINEAGE_CROSS_SPLIT,),
        soft_rules=(LeakageRule.CAPTURE_SESSION_CROSS_SPLIT,),
        waiver_allowed_rules=(),
        scene_grouping_enabled=False,
        temporal_overlap_enabled=False,
        capture_session_grouping_enabled=False,
    )
    report = check_leakage(
        items,
        policy,
        leakage_report_id=uuid4(),
        project_id=uuid4(),
        dataset_version_id=uuid4(),
        checked_at=NOW,
        source_snapshot_at=NOW,
    )
    assert report.violations == ()


def test_grouping_defaults_are_enabled_in_policy() -> None:
    """FR-030 两条分组默认开启：同会话/同对象跨划分是最隐蔽的泄漏来源。"""
    policy = leakage_policy()
    assert policy.capture_session_grouping_enabled is True
    assert policy.subject_grouping_enabled is True
    # 序列化进入 policy_sha256：开关状态是策略内容的一部分，改动必须换 Hash。
    payload = policy._payload()  # noqa: SLF001 - 测试直接校验落盘内容
    assert payload["capture_session_grouping_enabled"] is True
    assert payload["subject_grouping_enabled"] is True
