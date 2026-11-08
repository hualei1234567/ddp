"""数据集 HTTP 请求模型与响应序列化。

请求模型统一 ``extra="forbid"``；客户端不能提交状态、project_id、version、
Hash、审批人等权威字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.datasets.leakage import LeakageReport
from app.domain.datasets.manifest import Manifest
from app.domain.datasets.models import (
    Dataset,
    DatasetItem,
    DatasetVersion,
    SelectionPreview,
)
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.quality import QualityReport


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    purpose: str
    owner_id: UUID
    usage_policy: str
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    retention_policy: dict[str, object] | None = None
    default_dedup_policy_id: UUID | None = None
    default_split_policy_id: UUID | None = None
    default_leakage_policy_id: UUID | None = None


class DatasetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    purpose: str | None = None
    tags: list[str] | None = None
    usage_policy: str | None = None
    retention_policy: dict[str, object] | None = None
    default_dedup_policy_id: UUID | None = None
    default_split_policy_id: UUID | None = None
    default_leakage_policy_id: UUID | None = None


class DatasetActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None


class DedupPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    exact_keys: list[str] = Field(min_length=1)
    near_duplicate_enabled: bool = False
    near_duplicate_enforcement: str = "WARN_ONLY"
    similarity_method: str | None = Field(default=None, max_length=100)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)
    similarity_min_cluster_size: int | None = Field(default=None, ge=2)
    grouping_keys: list[str] = Field(default_factory=list)
    retain_rule: str = "NEWEST_ACCEPTED"


class SplitPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    strategy: str
    train_ratio: float = Field(default=0.8, ge=0, le=1)
    validation_ratio: float = Field(default=0.1, ge=0, le=1)
    test_ratio: float = Field(default=0.1, ge=0, le=1)
    train_count: int | None = Field(default=None, ge=1)
    validation_count: int | None = Field(default=None, ge=1)
    test_count: int | None = Field(default=None, ge=0)
    seed: int = Field(ge=0)
    stratify_by: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(min_length=1)
    empty_split_policy: str = "FAIL"
    min_item_count: int = Field(default=1, ge=1)
    min_duration_ns: int = Field(default=0, ge=0)
    rounding_rule: str = "LARGEST_REMAINDER"


class LeakagePolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    hard_rules: list[str] = Field(min_length=1)
    soft_rules: list[str] = Field(default_factory=list)
    waiver_allowed_rules: list[str] = Field(default_factory=list)
    similarity_grouping_enabled: bool = False
    scene_grouping_enabled: bool = True
    batch_grouping_enabled: bool = False
    temporal_overlap_enabled: bool = True


class QualityPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    min_selected_count: int = Field(default=1, ge=1)
    allow_warn_quality: bool = False
    require_leakage_pass: bool = False


class DatasetVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_rule_version: str = Field(min_length=1, max_length=64)
    selection_filters: dict[str, object] = Field(default_factory=dict)
    dedup_policy_id: UUID
    dedup_policy_version: int = Field(ge=1)
    split_policy_id: UUID
    split_policy_version: int = Field(ge=1)
    leakage_policy_id: UUID
    leakage_policy_version: int = Field(ge=1)
    quality_policy_id: UUID
    quality_policy_version: int = Field(ge=1)
    supersedes_version_id: UUID | None = None
    build_now: bool = False


class SelectionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_filters: dict[str, object] = Field(default_factory=dict)
    dedup_policy_id: UUID
    dedup_policy_version: int = Field(ge=1)
    split_policy_id: UUID
    split_policy_version: int = Field(ge=1)
    leakage_policy_id: UUID
    leakage_policy_version: int = Field(ge=1)
    quality_policy_id: UUID | None = None
    quality_policy_version: int | None = Field(default=None, ge=1)
    sample_limit: int = Field(default=50, ge=1, le=100)


class LeakageWaiverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    impact: str | None = Field(default=None, max_length=1000)
    expires_at: datetime | None = None


class VersionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None
    waivers: list[LeakageWaiverRequest] = Field(default_factory=list)


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def dataset_out(dataset: Dataset, *, allowed_actions: frozenset[str] = frozenset()) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "project_id": dataset.project_id,
        "dataset_lineage_id": dataset.dataset_lineage_id,
        "code": dataset.code,
        "name": dataset.name,
        "description": dataset.description,
        "purpose": _value(dataset.purpose),
        "owner_id": dataset.owner_id,
        "status": _value(dataset.status),
        "current_version_id": dataset.current_version_id,
        "tags": list(dataset.tags),
        "usage_policy": _value(dataset.usage_policy),
        "retention_policy": dataset.retention_policy,
        "default_dedup_policy_id": dataset.default_dedup_policy_id,
        "default_split_policy_id": dataset.default_split_policy_id,
        "default_leakage_policy_id": dataset.default_leakage_policy_id,
        "frozen_version_count": dataset.frozen_version_count,
        "archived_at": _iso(dataset.archived_at),
        "archived_by": dataset.archived_by,
        "archive_reason": dataset.archive_reason,
        "allowed_actions": sorted(allowed_actions),
        "version": dataset.row_version,
        "created_at": _iso(dataset.created_at),
        "updated_at": _iso(dataset.updated_at),
        "created_by": dataset.created_by,
        "updated_by": dataset.updated_by,
    }


def version_out(
    version: DatasetVersion,
    *,
    allowed_actions: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    return {
        "dataset_version_id": version.dataset_version_id,
        "project_id": version.project_id,
        "dataset_id": version.dataset_id,
        "dataset_lineage_id": version.dataset_lineage_id,
        "dataset_version": version.dataset_version,
        "status": _value(version.status),
        "validity_status": _value(version.validity_status),
        "selection_rule_version": version.selection_rule_version,
        "selection_filters": version.selection_filters,
        "selection_snapshot_at": _iso(version.selection_snapshot_at),
        "dedup_policy_ref": version.dedup_policy_ref.to_payload(),
        "split_policy_ref": version.split_policy_ref.to_payload(),
        "leakage_policy_ref": version.leakage_policy_ref.to_payload(),
        "quality_policy_ref": version.quality_policy_ref.to_payload(),
        "candidate_count": version.candidate_count,
        "selected_count": version.selected_count,
        "excluded_count": version.excluded_count,
        "duplicated_count": version.duplicated_count,
        "item_count": version.item_count,
        "train_count": version.train_count,
        "validation_count": version.validation_count,
        "test_count": version.test_count,
        "total_duration_ns": version.total_duration_ns,
        "total_bytes": version.total_bytes,
        "episode_version_digest": version.episode_version_digest,
        "annotation_schema_versions": list(version.annotation_schema_versions),
        "annotation_submission_refs_digest": version.annotation_submission_refs_digest,
        "manifest_key": version.manifest_key,
        "manifest_sha256": version.manifest_sha256,
        "quality_report_id": version.quality_report_id,
        "quality_report_sha256": version.quality_report_sha256,
        "leakage_report_id": version.leakage_report_id,
        "leakage_report_sha256": version.leakage_report_sha256,
        "leakage_status": _value(version.leakage_status) if version.leakage_status else None,
        "source_snapshot_at": _iso(version.source_snapshot_at),
        "build_attempt": version.build_attempt,
        "freeze_approval_id": version.freeze_approval_id,
        "frozen_by": version.frozen_by,
        "frozen_at": _iso(version.frozen_at),
        "supersedes_version_id": version.supersedes_version_id,
        "superseded_by_version_id": version.superseded_by_version_id,
        "archived_at": _iso(version.archived_at),
        "archive_reason": version.archive_reason,
        "blocking_issues": list(version.blocking_issues),
        "warnings": list(version.warnings),
        "allowed_actions": sorted(allowed_actions),
        "version": version.row_version,
        "created_at": _iso(version.created_at),
        "updated_at": _iso(version.updated_at),
        "created_by": version.created_by,
        "updated_by": version.updated_by,
    }


def item_out(item: DatasetItem) -> dict[str, Any]:
    return {
        "dataset_item_id": item.dataset_item_id,
        "project_id": item.project_id,
        "dataset_version_id": item.dataset_version_id,
        "episode_id": item.episode_id,
        "episode_version": item.episode_version,
        "episode_lineage_id": item.episode_lineage_id,
        "manifest_sha256": item.manifest_sha256,
        "raw_asset_ids": [str(value) for value in item.raw_asset_ids],
        "annotation_submission_id": item.annotation_submission_id,
        "annotation_version": item.annotation_version,
        "annotation_record_digest": item.annotation_record_digest,
        "schema_id": item.schema_id,
        "schema_version": item.schema_version,
        "schema_sha256": item.schema_sha256,
        "split": _value(item.split),
        "stratum": item.stratum,
        "sample_role": _value(item.sample_role),
        "weight": item.weight,
        "dedup_group_id": item.dedup_group_id,
        "similarity_group_id": item.similarity_group_id,
        "scene_group_id": item.scene_group_id,
        "time_range": item.time_range,
        "selected_reason": item.selected_reason,
        "item_sha256": item.item_sha256,
        "created_at": _iso(item.created_at),
    }


def preview_out(preview: SelectionPreview) -> dict[str, Any]:
    return {
        "preview_id": preview.preview_id,
        "project_id": preview.project_id,
        "dataset_id": preview.dataset_id,
        "dataset_version_id": preview.dataset_version_id,
        "candidate_count": preview.candidate_count,
        "selected_count": preview.selected_count,
        "excluded_count": preview.excluded_count,
        "duplicated_count": preview.duplicated_count,
        "split_counts": preview.split_counts,
        "split_preview_status": preview.split_preview_status,
        "exclusion_summary": list(preview.exclusion_summary),
        "items_sample": list(preview.items_sample),
        "warnings": list(preview.warnings),
        "blocking_issues": list(preview.blocking_issues),
        "source_snapshot_at": _iso(preview.source_snapshot_at),
        "preview_sha256": preview.preview_sha256,
        "created_at": _iso(preview.created_at),
    }


def policy_out(policy: DedupPolicy | SplitPolicy | LeakagePolicy | QualityPolicy) -> dict[str, Any]:
    base: dict[str, Any] = {
        "project_id": policy.project_id,
        "dataset_id": policy.dataset_id,
        "name": policy.name,
        "version": policy.version,
        "policy_sha256": policy.policy_sha256,
        "created_at": _iso(policy.created_at),
        "created_by": policy.created_by,
    }
    if isinstance(policy, DedupPolicy):
        base.update(
            dedup_policy_id=policy.dedup_policy_id,
            exact_keys=[item.value for item in policy.exact_keys],
            near_duplicate_enabled=policy.near_duplicate_enabled,
            near_duplicate_enforcement=policy.near_duplicate_enforcement.value,
            similarity_method=policy.similarity_method,
            similarity_threshold=policy.similarity_threshold,
            similarity_min_cluster_size=policy.similarity_min_cluster_size,
            grouping_keys=list(policy.grouping_keys),
            retain_rule=policy.retain_rule.value,
        )
    elif isinstance(policy, SplitPolicy):
        base.update(
            split_policy_id=policy.split_policy_id,
            strategy=policy.strategy.value,
            train_ratio=policy.train_ratio,
            validation_ratio=policy.validation_ratio,
            test_ratio=policy.test_ratio,
            train_count=policy.train_count,
            validation_count=policy.validation_count,
            test_count=policy.test_count,
            seed=policy.seed,
            stratify_by=[item.value for item in policy.stratify_by],
            group_by=[item.value for item in policy.group_by],
            empty_split_policy=policy.empty_split_policy.value,
            min_item_count=policy.min_item_count,
            min_duration_ns=policy.min_duration_ns,
            rounding_rule=policy.rounding_rule.value,
        )
    elif isinstance(policy, LeakagePolicy):
        base.update(
            leakage_policy_id=policy.leakage_policy_id,
            hard_rules=[item.value for item in policy.hard_rules],
            soft_rules=[item.value for item in policy.soft_rules],
            waiver_allowed_rules=[item.value for item in policy.waiver_allowed_rules],
            similarity_grouping_enabled=policy.similarity_grouping_enabled,
            scene_grouping_enabled=policy.scene_grouping_enabled,
            batch_grouping_enabled=policy.batch_grouping_enabled,
            temporal_overlap_enabled=policy.temporal_overlap_enabled,
        )
    else:
        base.update(
            quality_policy_id=policy.quality_policy_id,
            min_selected_count=policy.min_selected_count,
            allow_warn_quality=policy.allow_warn_quality,
            require_leakage_pass=policy.require_leakage_pass,
        )
    return base


def quality_out(report: QualityReport) -> dict[str, Any]:
    return report.to_payload()


def leakage_out(report: LeakageReport) -> dict[str, Any]:
    return report.to_payload()


def manifest_out(
    manifest: Manifest,
    *,
    inline: bool = True,
    download_url: str | None = None,
) -> dict[str, Any]:
    return manifest.to_payload(inline=inline, download_url=download_url)
