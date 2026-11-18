"""DatasetVersion 构建流水线。

选择、去重、划分、泄漏检查、质量报告与 Manifest 在同一确定性流程中完成；
任何门禁失败都产出可追溯的阻断项，绝不静默生成可冻结版本。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import sha256_text, sha256_value
from app.domain.datasets.dedup import DedupOutcome, deduplicate
from app.domain.datasets.enums import (
    DatasetUsagePolicy,
    LeakageStatus,
    QualityReportStatus,
    StratifyKey,
)
from app.domain.datasets.errors import (
    AnnotationNotApprovedError,
    AnnotationSnapshotMismatchError,
    ItemIneligibleError,
)
from app.domain.datasets.leakage import (
    LeakageItem,
    LeakageReport,
    LeakageWaiver,
    check_leakage,
)
from app.domain.datasets.manifest import Manifest, build_manifest
from app.domain.datasets.models import DatasetItem, DatasetVersion, SelectionPreview
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.quality import QualityReport, leakage_summary
from app.domain.datasets.quality_gate import EpisodeQualityVerdict
from app.domain.datasets.selection import (
    EpisodeCandidate,
    SelectionFilters,
    SelectionOutcome,
    contract_code_for_exclusion,
    evaluate_selection,
)
from app.domain.datasets.split import SplitOutcome, SplitStatus, assign_splits


@dataclass(frozen=True, slots=True)
class PipelineResult:
    version_updates: dict[str, object]
    preview: SelectionPreview
    items: tuple[DatasetItem, ...]
    leakage_report: LeakageReport
    quality_report: QualityReport
    manifest: Manifest | None
    warnings: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    split_status: SplitStatus


def _detect_near_duplicates(
    selected: tuple[EpisodeCandidate, ...],
    dedup_policy: DedupPolicy,
):
    """按策略参数跑近重复检测；策略未给参数时用领域默认值。"""
    from app.domain.datasets.near_duplicate import (
        DEFAULT_DURATION_TOLERANCE,
        DEFAULT_OVERLAP_RATIO,
        NearDuplicateMethod,
        detect_near_duplicates,
    )

    method = dedup_policy.similarity_method or NearDuplicateMethod.TEMPORAL_OVERLAP
    # 策略里的 similarity_threshold 是"相似度阈值"，与我们的"重叠率阈值"
    # 语义对齐：阈值越高越严格。直接复用，不另立字段。
    overlap = (
        dedup_policy.similarity_threshold
        if dedup_policy.similarity_threshold is not None
        else DEFAULT_OVERLAP_RATIO
    )
    min_cluster = dedup_policy.similarity_min_cluster_size or 2
    return detect_near_duplicates(
        selected,
        method=method,
        overlap_ratio=overlap,
        duration_tolerance=DEFAULT_DURATION_TOLERANCE,
        min_cluster_size=min_cluster,
    )


def run_pipeline(
    *,
    base_version: DatasetVersion,
    candidates: tuple[EpisodeCandidate, ...],
    filters: SelectionFilters,
    usage_policy: DatasetUsagePolicy | str,
    dedup_policy: DedupPolicy,
    split_policy: SplitPolicy,
    leakage_policy: LeakagePolicy,
    quality_policy: QualityPolicy,
    leakage_report_id: UUID,
    quality_report_id: UUID,
    now: datetime,
    source_snapshot_at: datetime,
    waivers: tuple[LeakageWaiver, ...] = (),
    sample_limit: int = 50,
    require_explicit_candidates: bool = False,
    quality_verdicts: Mapping[UUID, EpisodeQualityVerdict] | None = None,
) -> PipelineResult:
    selection = evaluate_selection(
        candidates,
        filters=filters,
        usage_policy=usage_policy,
        quality_verdicts=quality_verdicts,
    )
    if require_explicit_candidates:
        # 显式指名条目（selection_filters.episode_ids 非空）时，"提名即承诺"：
        # 全部被排除说明调用方引用了不合格条目，必须确定性地拒绝，而不是
        # 静默返回一个空版本或只看统计。这是 dataset_item_ineligible /
        # dataset_annotation_not_approved / ..._snapshot_mismatch 的对外判定点
        # （另一处是 _ensure_annotation，用于防御手工构造的候选绕过门禁）。
        _reject_ineligible_explicit_selection(selection, filters)
    dedup = deduplicate(selection.selected, dedup_policy)
    split = assign_splits(dedup.selected, split_policy)
    candidate_by_id = {item.episode_id: item for item in candidates}
    items = _build_items(
        dedup.selected,
        split,
        candidate_by_id=candidate_by_id,
        dataset_version_id=base_version.dataset_version_id,
        project_id=base_version.project_id,
        dedup=dedup,
        now=now,
    )
    warnings = list(selection.warnings) + list(split.warnings)
    blocking: list[str] = []
    if not dedup.selected:
        blocking.append("DATASET_EMPTY")
    if split.status is not SplitStatus.OK:
        blocking.append(f"SPLIT_{split.status.value}")
    near_duplicate_summary: dict[str, object] | None = None
    if dedup_policy.near_duplicate_enabled:
        near = _detect_near_duplicates(dedup.selected, dedup_policy)
        near_duplicate_summary = near.summary()
        if near.found:
            message = f"NEAR_DUPLICATE_SUSPECTED:{near.cluster_count} 簇 / {len(near.pairs)} 对"
            if dedup_policy.near_duplicate_enforcement.value == "BLOCK":
                blocking.append(message)
            else:
                warnings.append(message)
        else:
            warnings.append("NEAR_DUPLICATE_NONE")

    leakage_items = _leakage_items(dedup.selected, split, candidate_by_id)
    leakage_report = check_leakage(
        leakage_items,
        leakage_policy,
        leakage_report_id=leakage_report_id,
        project_id=base_version.project_id,
        dataset_version_id=base_version.dataset_version_id,
        checked_at=now,
        waivers=waivers,
        source_snapshot_at=source_snapshot_at,
        report_key=_report_key(base_version, "leakage"),
    )
    if leakage_report.leakage_status in {LeakageStatus.FAIL, LeakageStatus.UNKNOWN}:
        blocking.append(f"LEAKAGE_{leakage_report.leakage_status.value}")
    elif leakage_report.leakage_status is LeakageStatus.WARN:
        warnings.append("LEAKAGE_WARN")

    total_duration = sum(item.duration_ns for item in dedup.selected)
    total_bytes = sum(item.total_bytes for item in dedup.selected)
    distribution = _distributions(dedup.selected, split, candidate_by_id)
    coverage = _coverage(items, split_policy.stratify_by)
    dedup_summary = {
        "exact_group_count": len(dedup.groups),
        "excluded_duplicate_count": dedup.duplicated_count,
        "near_duplicate_status": ("UNKNOWN" if dedup_policy.near_duplicate_enabled else "DISABLED"),
    }
    leakage_summary_payload = leakage_summary(
        leakage_report.leakage_status,
        leakage_report.hard_violation_count,
        leakage_report.soft_violation_count,
        leakage_report.report_sha256,
    )
    if len(dedup.selected) < quality_policy.min_selected_count:
        blocking.append("INSUFFICIENT_SELECTED_COUNT")
    if quality_policy.allow_warn_quality is False and any(
        item.quality_status == "WARN" for item in dedup.selected
    ):
        blocking.append("QUALITY_POLICY_WARN_NOT_ALLOWED")
    if (
        quality_policy.require_leakage_pass
        and leakage_report.leakage_status is not LeakageStatus.PASS
    ):
        blocking.append("QUALITY_POLICY_LEAKAGE_NOT_PASS")
    quality_report = QualityReport(
        quality_report_id=quality_report_id,
        project_id=base_version.project_id,
        dataset_version_id=base_version.dataset_version_id,
        quality_policy_ref=base_version.quality_policy_ref,
        candidate_count=selection.candidate_count,
        selected_count=len(dedup.selected),
        excluded_count=selection.excluded_count,
        duplicated_count=dedup.duplicated_count,
        split_counts=split.counts_payload(),
        distributions=distribution,
        total_duration_ns=total_duration,
        total_bytes=total_bytes,
        coverage=coverage,
        dedup_summary=dedup_summary,
        leakage_summary=leakage_summary_payload,
        warnings=tuple(warnings),
        blocking_issues=tuple(blocking),
        source_snapshot_at=source_snapshot_at,
        created_at=now,
        report_key=_report_key(base_version, "quality"),
    )
    if quality_report.status is QualityReportStatus.FAILED:
        for issue in quality_report.blocking_issues:
            if issue not in blocking:
                blocking.append(issue)

    episode_digest = _episode_version_digest(dedup.selected)
    submission_digest = _submission_digest(items)
    schema_versions = _schema_versions(items)
    version_updates: dict[str, object] = {
        "candidate_count": selection.candidate_count,
        "selected_count": len(dedup.selected),
        "excluded_count": selection.excluded_count,
        "duplicated_count": dedup.duplicated_count,
        "item_count": len(items),
        "train_count": split.train_count,
        "validation_count": split.validation_count,
        "test_count": split.test_count,
        "total_duration_ns": total_duration,
        "total_bytes": total_bytes,
        "episode_version_digest": episode_digest,
        "annotation_schema_versions": schema_versions,
        "annotation_submission_refs_digest": submission_digest,
        "quality_report_id": quality_report_id,
        "quality_report_sha256": quality_report.report_sha256,
        "leakage_report_id": leakage_report_id,
        "leakage_report_sha256": leakage_report.report_sha256,
        "leakage_status": leakage_report.leakage_status,
        "source_snapshot_at": source_snapshot_at,
        "warnings": tuple(dict.fromkeys(warnings)),
        "blocking_issues": tuple(dict.fromkeys(blocking)),
        "exclusion_summary": _exclusion_summary(
            selection.exclusion_summary, dedup, near_duplicate_summary
        ),
        "updated_at": now,
    }
    manifest: Manifest | None = None
    if split.status is SplitStatus.OK and items and not blocking:
        provisional = replace(base_version, **version_updates)
        manifest = build_manifest(
            provisional,
            items,
            episode_version_digest=episode_digest,
            annotation_submission_refs_digest=submission_digest,
            annotation_schema_versions=schema_versions,
            created_at=now,
        )
        version_updates["manifest_key"] = _manifest_key(base_version)
        version_updates["manifest_sha256"] = manifest.manifest_sha256
        manifest = replace(manifest, manifest_key=_manifest_key(base_version))
    else:
        version_updates["manifest_key"] = None
        version_updates["manifest_sha256"] = None

    preview = _preview(
        base_version,
        selection,
        dedup,
        split,
        samples=_preview_samples(selection, dedup, split, limit=sample_limit),
        now=now,
        source_snapshot_at=source_snapshot_at,
        blocking=blocking,
        warnings=warnings,
    )
    return PipelineResult(
        version_updates=version_updates,
        preview=preview,
        items=items if split.status is SplitStatus.OK else (),
        leakage_report=leakage_report,
        quality_report=quality_report,
        manifest=manifest,
        warnings=tuple(dict.fromkeys(warnings)),
        blocking_issues=tuple(dict.fromkeys(blocking)),
        split_status=split.status,
    )


def _build_items(
    selected: tuple[EpisodeCandidate, ...],
    split: SplitOutcome,
    *,
    candidate_by_id: dict[UUID, EpisodeCandidate],
    dataset_version_id: UUID,
    project_id: UUID,
    dedup: DedupOutcome,
    now: datetime,
) -> tuple[DatasetItem, ...]:
    if split.status is not SplitStatus.OK:
        return ()
    items: list[DatasetItem] = []
    for candidate in selected:
        assignment = split.assignments.get(candidate.episode_id)
        if assignment is None:
            continue
        _ensure_annotation(candidate)
        items.append(
            DatasetItem(
                dataset_item_id=_item_id(dataset_version_id, candidate.episode_id),
                project_id=project_id,
                dataset_version_id=dataset_version_id,
                episode_id=candidate.episode_id,
                episode_version=candidate.episode_version,
                episode_lineage_id=candidate.episode_lineage_id,
                manifest_sha256=candidate.manifest_sha256 or "",
                raw_asset_ids=candidate.raw_asset_ids,
                annotation_submission_id=candidate.annotation_submission_id or UUID(int=0),
                annotation_version=candidate.annotation_version or 0,
                annotation_record_digest=candidate.annotation_record_digest or "",
                schema_id=candidate.schema_id or UUID(int=0),
                schema_version=candidate.schema_version or 0,
                schema_sha256=candidate.schema_sha256 or "",
                split=assignment,
                stratum=split.strata.get(candidate.episode_id),
                dedup_group_id=dedup.dedup_group_by_episode.get(candidate.episode_id),
                scene_group_id=candidate.scene_group_id,
                time_range={"start_ns": 0, "end_ns": candidate.duration_ns},
                created_at=now,
            )
        )
    items.sort(key=lambda item: str(item.episode_id))
    return tuple(items)


def _ensure_annotation(candidate: EpisodeCandidate) -> None:
    # 选择门禁已保证；这里只做防御，避免手工构造候选绕过。
    # 契约要求"引用未批准标注"必须是可区分的确定性错误，不能退化为 500。
    if candidate.annotation_status != "APPROVED" or (
        candidate.annotation_submission_id is None
        or candidate.annotation_version is None
        or candidate.annotation_record_digest is None
    ):
        raise AnnotationNotApprovedError(
            "入选 Episode 缺少已批准标注快照",
            details={"episode_ids": [str(candidate.episode_id)]},
        )
    if (
        candidate.schema_id is None
        or candidate.schema_version is None
        or candidate.schema_sha256 is None
    ):
        raise AnnotationSnapshotMismatchError(
            "入选 Episode 缺少标注/Schema 版本快照",
            details={"episode_ids": [str(candidate.episode_id)]},
        )


def _reject_ineligible_explicit_selection(
    selection: SelectionOutcome,
    filters: SelectionFilters,
) -> None:
    """显式指名条目全部不合格时确定性拒绝（逐码归类，不混用错误码）。

    只有"调用方点名的条目没有任何一个入选"才拒绝：
    - 部分入选 + 部分被排除是正常统计结果，不报错；
    - 未点名条目（未传 ``episode_ids``）时"全部排除"属数据不足，
      由既有的 ``DATASET_EMPTY`` / ``dataset_empty`` 处理，不走本分支。
    """
    if selection.selected or not filters.episode_ids:
        return
    by_code: dict[str, list[str]] = {}
    for item in selection.excluded:
        contract_code = contract_code_for_exclusion(item.code)
        if contract_code is None:
            continue
        by_code.setdefault(contract_code, []).append(str(item.episode_id))
    # 优先级：引用未批准标注是"前置输入错误"，先于泛化的不合格上报。
    if "dataset_annotation_snapshot_mismatch" in by_code:
        raise AnnotationSnapshotMismatchError(
            "所指名 Episode 的标注 Schema 快照与提交冻结快照不一致",
            details={
                "episode_ids": sorted(by_code["dataset_annotation_snapshot_mismatch"]),
                "requested_episode_ids": [str(item) for item in filters.episode_ids],
            },
        )
    if "dataset_annotation_not_approved" in by_code:
        raise AnnotationNotApprovedError(
            "所指名的 Episode 不存在已批准标注",
            details={
                "episode_ids": sorted(by_code["dataset_annotation_not_approved"]),
                "requested_episode_ids": [str(item) for item in filters.episode_ids],
            },
        )
    if "dataset_item_ineligible" in by_code:
        raise ItemIneligibleError(
            "所指名的 Episode 均不合格",
            details={
                "episode_ids": sorted(by_code["dataset_item_ineligible"]),
                "requested_episode_ids": [str(item) for item in filters.episode_ids],
            },
        )
    # 只剩去重类排除（例如全部互为重复）时不是"不合格"，交由空集/划分逻辑处理。


def _leakage_items(
    selected: tuple[EpisodeCandidate, ...],
    split: SplitOutcome,
    candidate_by_id: dict[UUID, EpisodeCandidate],
) -> tuple[LeakageItem, ...]:
    result: list[LeakageItem] = []
    for candidate in selected:
        assignment = split.assignments.get(candidate.episode_id)
        if assignment is None:
            continue
        result.append(
            LeakageItem(
                episode_id=candidate.episode_id,
                episode_lineage_id=candidate.episode_lineage_id,
                manifest_sha256=candidate.manifest_sha256 or "",
                raw_asset_ids=candidate.raw_asset_ids,
                raw_asset_hashes=candidate.raw_asset_hashes,
                split=assignment,
                annotation_submission_id=candidate.annotation_submission_id or UUID(int=0),
                annotation_record_digest=candidate.annotation_record_digest or "",
                scene_group_id=candidate.scene_group_id,
                batch_id=candidate.batch_id,
                start_time_ns=candidate.start_time_ns,
                end_time_ns=candidate.end_time_ns,
            )
        )
    return tuple(result)


def _distributions(
    selected: tuple[EpisodeCandidate, ...],
    split: SplitOutcome,
    candidate_by_id: dict[UUID, EpisodeCandidate],
) -> dict[str, dict[str, int]]:
    scene: Counter[str] = Counter()
    robot: Counter[str] = Counter()
    device: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    for candidate in selected:
        for label in candidate.scene_labels:
            scene[label] += 1
        if candidate.robot_id is not None:
            robot[str(candidate.robot_id)] += 1
        for device_id in candidate.device_ids:
            device[str(device_id)] += 1
        quality[candidate.quality_status] += 1
    return {
        "scene": dict(sorted(scene.items())),
        "action": {},
        "robot": dict(sorted(robot.items())),
        "device": dict(sorted(device.items())),
        "label": {},
        "quality_status": dict(sorted(quality.items())),
    }


def _coverage(
    items: tuple[DatasetItem, ...],
    keys: tuple[StratifyKey, ...],
) -> dict[str, object]:
    if not keys:
        return {
            "status": "N/A",
            "required_dimensions": 0,
            "covered_dimensions": 0,
            "details": [],
        }
    details: list[dict[str, object]] = []
    covered = 0
    for key in keys:
        available = sum(1 for item in items if item.stratum and f"{key.value}=" in item.stratum)
        if available == len(items) and items:
            covered += 1
        details.append({"dimension": key.value, "covered_items": available})
    status = "OK" if covered == len(keys) else "PARTIAL"
    return {
        "status": status,
        "required_dimensions": len(keys),
        "covered_dimensions": covered,
        "details": details,
    }


def _exclusion_summary(
    selection_summary: tuple[dict[str, object], ...],
    dedup: DedupOutcome,
    near_duplicate_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    summary = [dict(item) for item in selection_summary]
    if dedup.duplicated_count:
        summary.append(
            {
                "code": "DUPLICATE",
                "count": dedup.duplicated_count,
                "sample_episode_ids": [str(item.episode_id) for item in dedup.excluded[:5]],
            }
        )
    result: dict[str, object] = {"items": summary}
    if near_duplicate_summary is not None:
        # 近重复结论冻结进版本的 exclusion_summary：事后能回答"这版数据在
        # 构建时有没有疑似近重复、用的是哪套阈值"，而不是只剩一句警告。
        result["near_duplicate"] = near_duplicate_summary
    return result


def _preview_samples(
    selection,
    dedup: DedupOutcome,
    split: SplitOutcome,
    *,
    limit: int,
) -> tuple[dict[str, object], ...]:
    samples: list[dict[str, object]] = []
    for candidate in dedup.selected:
        samples.append(
            {
                "episode_id": str(candidate.episode_id),
                "episode_version": candidate.episode_version,
                "split": (
                    split.assignments[candidate.episode_id].value
                    if candidate.episode_id in split.assignments
                    else None
                ),
                "decision": "SELECTED",
                "reason": None,
            }
        )
    for item in dedup.excluded:
        samples.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": selection_episode_version(selection, item.episode_id),
                "split": None,
                "decision": "DUPLICATED",
                "reason": item.reason,
            }
        )
    for item in selection.excluded:
        samples.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": selection_episode_version(selection, item.episode_id),
                "split": None,
                "decision": "EXCLUDED",
                "reason": item.reason,
            }
        )
    samples.sort(key=lambda item: str(item["episode_id"]))
    return tuple(samples[:limit])


def selection_episode_version(selection, episode_id: UUID) -> int:
    for candidate in selection.candidates:
        if candidate.episode_id == episode_id:
            return candidate.episode_version
    return 1


def _preview(
    base_version: DatasetVersion,
    selection,
    dedup: DedupOutcome,
    split: SplitOutcome,
    *,
    samples: tuple[dict[str, object], ...],
    now: datetime,
    source_snapshot_at: datetime,
    blocking: list[str],
    warnings: list[str],
) -> SelectionPreview:
    return SelectionPreview(
        project_id=base_version.project_id,
        dataset_id=base_version.dataset_id,
        dataset_version_id=base_version.dataset_version_id,
        candidate_count=selection.candidate_count,
        selected_count=len(dedup.selected),
        excluded_count=selection.excluded_count,
        duplicated_count=dedup.duplicated_count,
        split_counts=split.counts_payload(),
        split_preview_status=split.status.value,
        exclusion_summary=tuple(_exclusion_summary(selection.exclusion_summary, dedup)["items"]),
        warnings=tuple(dict.fromkeys(warnings)),
        blocking_issues=tuple(dict.fromkeys(blocking)),
        source_snapshot_at=source_snapshot_at,
        items_sample=samples,
        created_at=now,
    )


def _episode_version_digest(selected: tuple[EpisodeCandidate, ...]) -> str:
    payload = [
        f"{item.episode_id}:{item.episode_version}"
        for item in sorted(selected, key=lambda candidate: str(candidate.episode_id))
    ]
    return sha256_text("\n".join(payload))


def _submission_digest(items: tuple[DatasetItem, ...]) -> str | None:
    if not items:
        return None
    payload = [
        {
            "submission_id": str(item.annotation_submission_id),
            "annotation_version": item.annotation_version,
            "record_digest": item.annotation_record_digest,
            "schema_id": str(item.schema_id),
            "schema_version": item.schema_version,
            "schema_sha256": item.schema_sha256,
        }
        for item in sorted(items, key=lambda item: str(item.episode_id))
    ]
    return sha256_value(payload)


def _schema_versions(items: tuple[DatasetItem, ...]) -> tuple[dict[str, object], ...]:
    unique: dict[tuple[str, int, str], dict[str, object]] = {}
    for item in items:
        key = (str(item.schema_id), item.schema_version, item.schema_sha256)
        unique[key] = {
            "schema_id": str(item.schema_id),
            "schema_version": item.schema_version,
            "schema_sha256": item.schema_sha256,
        }
    return tuple(unique[key] for key in sorted(unique))


def _item_id(dataset_version_id: UUID, episode_id: UUID) -> UUID:
    from uuid import uuid5

    return uuid5(dataset_version_id, str(episode_id))


def _manifest_key(version: DatasetVersion) -> str:
    return (
        f"datasets/{version.project_id}/{version.dataset_id}/"
        f"{version.dataset_version_id}/manifest.json"
    )


def _report_key(version: DatasetVersion, kind: str) -> str:
    return (
        f"datasets/{version.project_id}/{version.dataset_id}/"
        f"{version.dataset_version_id}/{kind}-report.json"
    )


__all__ = ["PipelineResult", "run_pipeline", "selection_episode_version"]
