"""数据集域响应模型（阶段 4）。

**为什么单独成文件**：`dataset_schemas.py` 已经承载请求模型 + 序列化函数
（406 行），再塞 12 个 Out 模型会突破 500 行评审阈值。这里只放响应模型，
序列化函数仍留在原处，避免一次改动触碰太多既有代码。

**字段口径**：以 `packages/contracts/schemas/*.json` 为准逐字段对齐。
可空字段一律 `| None` 且给默认值 ``None``——契约写 `["X", "null"]` 时
语义是"必定出现、值可为 null"，Pydantic 输出该键，二者等价。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RetentionPolicyOut(BaseModel):
    mode: str | None = None
    retain_until: datetime | None = None
    note: str | None = None


class PolicyRefOut(BaseModel):
    policy_id: UUID
    version: int
    policy_sha256: str


class DatasetOut(BaseModel):
    dataset_id: UUID
    project_id: UUID
    dataset_lineage_id: UUID
    code: str
    name: str
    description: str | None = None
    purpose: str
    owner_id: UUID
    status: str
    current_version_id: UUID | None = None
    tags: list[str] = []
    usage_policy: str
    retention_policy: RetentionPolicyOut | None = None
    default_dedup_policy_id: UUID | None = None
    default_split_policy_id: UUID | None = None
    default_leakage_policy_id: UUID | None = None
    frozen_version_count: int = 0
    archived_at: datetime | None = None
    archived_by: UUID | None = None
    archive_reason: str | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    updated_by: UUID | None = None


class DatasetVersionOut(BaseModel):
    dataset_version_id: UUID
    project_id: UUID
    dataset_id: UUID
    dataset_lineage_id: UUID
    dataset_version: int
    status: str
    validity_status: str
    selection_rule_version: str
    selection_filters: dict[str, Any]
    selection_snapshot_at: datetime
    dedup_policy_ref: PolicyRefOut
    split_policy_ref: PolicyRefOut
    leakage_policy_ref: PolicyRefOut
    quality_policy_ref: PolicyRefOut
    candidate_count: int
    selected_count: int
    excluded_count: int
    duplicated_count: int
    item_count: int
    train_count: int
    validation_count: int
    test_count: int
    total_duration_ns: int | None = None
    total_bytes: int | None = None
    episode_version_digest: str | None = None
    annotation_schema_versions: list[SchemaSnapshotOut] = []
    annotation_submission_refs_digest: str | None = None
    manifest_key: str | None = None
    manifest_sha256: str | None = None
    quality_report_id: UUID | None = None
    quality_report_sha256: str | None = None
    leakage_report_id: UUID | None = None
    leakage_report_sha256: str | None = None
    leakage_status: str | None = None
    source_snapshot_at: datetime | None = None
    build_attempt: int
    freeze_approval_id: UUID | None = None
    frozen_by: UUID | None = None
    frozen_at: datetime | None = None
    supersedes_version_id: UUID | None = None
    superseded_by_version_id: UUID | None = None
    archived_at: datetime | None = None
    archive_reason: str | None = None
    blocking_issues: list[str] = []
    warnings: list[str] = []
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    updated_by: UUID | None = None


class SplitCountsOut(BaseModel):
    train: int
    validation: int
    test: int


class PolicyRefsOut(BaseModel):
    dedup: PolicyRefOut
    split: PolicyRefOut
    leakage: PolicyRefOut
    quality: PolicyRefOut


class TimeRangeOut(BaseModel):
    start_ns: int = 0
    end_ns: int = 0


class DatasetItemOut(BaseModel):
    dataset_item_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    episode_id: UUID
    episode_version: int
    episode_lineage_id: UUID
    manifest_sha256: str
    raw_asset_ids: list[str]
    annotation_submission_id: UUID
    annotation_version: int
    annotation_record_digest: str
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    split: str
    stratum: str | None = None
    sample_role: str
    weight: float
    dedup_group_id: UUID | None = None
    similarity_group_id: UUID | None = None
    scene_group_id: UUID | None = None
    time_range: TimeRangeOut | None = None
    selected_reason: str
    item_sha256: str
    created_at: datetime


class ExclusionSummaryEntryOut(BaseModel):
    code: str
    count: int
    sample_episode_ids: list[str] = []


class SelectionPreviewItemOut(BaseModel):
    episode_id: str
    episode_version: int
    split: str | None = None
    decision: str
    reason: str | None = None


class SelectionPreviewOut(BaseModel):
    preview_id: UUID | None = None
    project_id: UUID
    dataset_id: UUID
    dataset_version_id: UUID | None = None
    candidate_count: int
    selected_count: int
    excluded_count: int
    duplicated_count: int
    split_counts: SplitCountsOut
    split_preview_status: str
    exclusion_summary: list[ExclusionSummaryEntryOut]
    items_sample: list[SelectionPreviewItemOut] = []
    warnings: list[str]
    blocking_issues: list[str]
    source_snapshot_at: datetime
    preview_sha256: str
    created_at: datetime | None = None


class DedupPolicyOut(BaseModel):
    dedup_policy_id: UUID
    project_id: UUID
    name: str | None = None
    version: int
    exact_keys: list[str]
    near_duplicate_enabled: bool
    near_duplicate_enforcement: str
    similarity_method: str | None = None
    similarity_threshold: float | None = None
    similarity_min_cluster_size: int | None = None
    grouping_keys: list[str] = []
    retain_rule: str
    policy_sha256: str
    created_at: datetime
    created_by: UUID


class SplitPolicyOut(BaseModel):
    split_policy_id: UUID
    project_id: UUID
    name: str | None = None
    version: int
    strategy: str
    train_ratio: float = 0.0
    validation_ratio: float = 0.0
    test_ratio: float = 0.0
    train_count: int | None = None
    validation_count: int | None = None
    test_count: int | None = None
    seed: int
    stratify_by: list[str] = []
    group_by: list[str]
    empty_split_policy: str
    empty_test_waiver_required: bool = False
    min_item_count: int
    min_duration_ns: int
    rounding_rule: str
    policy_sha256: str
    created_at: datetime
    created_by: UUID


class LeakagePolicyOut(BaseModel):
    leakage_policy_id: UUID
    project_id: UUID
    name: str | None = None
    version: int
    hard_rules: list[str]
    soft_rules: list[str]
    waiver_allowed_rules: list[str]
    similarity_grouping_enabled: bool = False
    scene_grouping_enabled: bool = True
    batch_grouping_enabled: bool = False
    temporal_overlap_enabled: bool = True
    unknown_blocks: bool = True
    policy_sha256: str
    created_at: datetime
    created_by: UUID


class QualityPolicyOut(BaseModel):
    quality_policy_id: UUID
    project_id: UUID
    dataset_id: UUID
    name: str | None = None
    version: int
    min_selected_count: int
    allow_warn_quality: bool
    require_leakage_pass: bool
    policy_sha256: str
    created_at: datetime
    created_by: UUID


class CoverageDetailOut(BaseModel):
    dimension: str | None = None
    covered_items: int | None = None


class CoverageOut(BaseModel):
    status: str
    required_dimensions: int
    covered_dimensions: int
    details: list[dict[str, Any]]


class DistributionsOut(BaseModel):
    scene: dict[str, int] = {}
    action: dict[str, int] = {}
    robot: dict[str, int] = {}
    device: dict[str, int] = {}
    label: dict[str, int] = {}
    quality_status: dict[str, int] = {}


class DedupSummaryOut(BaseModel):
    exact_group_count: int
    excluded_duplicate_count: int
    near_duplicate_status: str


class LeakageSummaryOut(BaseModel):
    status: str
    hard_violation_count: int
    soft_violation_count: int
    report_sha256: str | None = None


class QualityReportOut(BaseModel):
    quality_report_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    status: str
    quality_policy_ref: PolicyRefOut
    candidate_count: int
    selected_count: int
    excluded_count: int
    duplicated_count: int
    split_counts: SplitCountsOut
    distributions: DistributionsOut
    total_duration_ns: int
    total_bytes: int
    coverage: CoverageOut
    dedup_summary: DedupSummaryOut
    leakage_summary: LeakageSummaryOut
    warnings: list[str]
    blocking_issues: list[str]
    source_snapshot_at: datetime
    generator_version: str
    report_sha256: str
    report_key: str | None = None
    created_at: datetime
    version: int


class LeakageObjectRefOut(BaseModel):
    object_type: str
    object_id: str


class LeakageViolationOut(BaseModel):
    rule: str
    level: str
    split_a: str
    split_b: str
    group_key: str | None = None
    object_refs: list[LeakageObjectRefOut]
    evidence: str | None = None


class LeakageWaiverOut(BaseModel):
    rule: str
    approved_by: str
    reason: str
    impact: str | None = None
    approved_at: str
    expires_at: str | None = None
    audit_ref: str


class LeakageReportOut(BaseModel):
    leakage_report_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    status: str
    leakage_status: str
    policy_ref: PolicyRefOut
    checked_rule_count: int
    violation_count: int
    hard_violation_count: int
    soft_violation_count: int
    violations: list[LeakageViolationOut]
    waivers: list[LeakageWaiverOut]
    unknown_reason: str | None = None
    checked_at: datetime
    source_snapshot_at: datetime | None = None
    report_sha256: str
    report_key: str | None = None
    version: int


class SchemaSnapshotOut(BaseModel):
    schema_id: str
    schema_version: int
    schema_sha256: str


class ManifestOut(BaseModel):
    schema_version: str
    dataset_version_id: UUID
    dataset_lineage_id: UUID
    dataset_version: int
    project_id: UUID
    created_by: UUID
    created_at: datetime
    policy_refs: PolicyRefsOut
    selection_rule_version: str | None = None
    source_snapshot_at: datetime
    items: list[DatasetItemOut]
    split_counts: SplitCountsOut
    quality_report_sha256: str
    leakage_report_sha256: str
    annotation_schema_versions: list[SchemaSnapshotOut]
    episode_version_digest: str
    annotation_submission_refs_digest: str | None = None
    canonicalization: str
    manifest_sha256: str
    manifest_key: str | None = None
    size_bytes: int | None = None
    inline: bool = True
    download_url: str | None = None
    download_url_expires_at: datetime | None = None


class ExportJobOut(BaseModel):
    export_job_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    status: str
    format: str
    format_version: str
    exporter_id: str
    exporter_version: str
    mapping_version: str
    config: dict[str, Any] = {}
    config_sha256: str
    idempotency_key: str
    requested_by: UUID
    output_prefix: str
    artifact_count: int | None = None
    total_bytes: int | None = None
    artifact_manifest_key: str | None = None
    artifact_manifest_sha256: str | None = None
    aggregate_sha256: str | None = None
    attempt: int
    max_attempts: int
    timeout_seconds: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    error_code: str | None = None
    error_summary: str | None = None
    log_ref: str | None = None
    verification_status: str
    verification_evidence_ref: str | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime


class ExportArtifactEntryOut(BaseModel):
    path: str
    size_bytes: int
    sha256: str
    content_type: str
    episode_refs: list[str] = []
    time_range: dict[str, Any] | None = None
    sidecar: bool = False


class ExportArtifactManifestOut(BaseModel):
    export_job_id: str
    format: str
    format_version: str
    exporter_version: str
    mapping_version: str
    config_sha256: str
    artifact_count: int
    total_bytes: int
    artifacts: list[ExportArtifactEntryOut]
    aggregate_sha256: str
    verification_status: str = "UNVERIFIED"


class ExportArtifactDownloadOut(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    download_url: str
    download_url_expires_at: str


class DatasetGrantOut(BaseModel):
    grant_id: UUID
    project_id: UUID
    dataset_id: UUID
    dataset_version_id: UUID | None = None
    grantee_project_id: UUID
    grantee_team: str | None = None
    scope: list[str]
    status: str
    reason: str
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None
    revoke_reason: str | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime | None = None
    created_by: UUID


class TrainingRequestOut(BaseModel):
    training_request_id: UUID
    project_id: UUID
    code: str
    title: str
    status: str
    current_revision_id: UUID | None = None
    current_revision_no: int | None = None
    priority: str
    owner_id: UUID
    blocked_reason: str | None = None
    blocked_from_status: str | None = None
    cancel_reason: str | None = None
    closed_at: datetime | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    updated_by: UUID | None = None


class RequiredSchemaVersionOut(BaseModel):
    schema_id: str
    schema_version: int


class SplitRequirementsOut(BaseModel):
    train_ratio: float | None = None
    validation_ratio: float | None = None
    test_ratio: float | None = None
    group_by: list[str] = []


class RequirementSnapshotOut(BaseModel):
    business_goal: str
    model_type: str
    training_type: str
    data_modalities: list[str]
    scene_targets: list[str]
    action_targets: list[str]
    robot_targets: list[str]
    device_targets: list[str]
    required_labels: list[str]
    required_schema_versions: list[RequiredSchemaVersionOut]
    target_episode_count: int | None = None
    target_duration_ns: int | None = None
    quality_thresholds: dict[str, Any]
    split_requirements: SplitRequirementsOut | None = None
    leakage_policy_id: str | None = None
    format: str
    format_version: str
    delivery_due_at: datetime
    acceptance_method: str
    priority: str


class TrainingRequestRevisionOut(BaseModel):
    revision_id: UUID
    training_request_id: UUID
    project_id: UUID
    revision_no: int
    status: str
    snapshot: RequirementSnapshotOut
    change_reason: str | None = None
    supersedes_revision_id: UUID | None = None
    submitted_by: UUID | None = None
    submitted_at: datetime | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    revision_sha256: str
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    created_by: UUID


class TrainingDeliveryOut(BaseModel):
    training_delivery_id: UUID
    project_id: UUID
    training_request_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    export_job_id: UUID
    delivery_note_no: str
    recipient_id: UUID
    recipient_team: str | None = None
    artifact_manifest_key: str | None = None
    artifact_manifest_sha256: str
    aggregate_sha256: str
    item_count: int
    split_counts: SplitCountsOut
    format: str | None = None
    format_version: str | None = None
    quality_report_ref: str | None = None
    status: str
    delivered_at: datetime | None = None
    accepted_by: UUID | None = None
    accepted_at: datetime | None = None
    acceptance_opinion: str | None = None
    rejection_reason: str | None = None
    rework_ref: UUID | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime
    updated_at: datetime | None = None
    created_by: UUID


class ModelRefOut(BaseModel):
    model_id: str = ""
    model_version: str | None = None
    registered_at: datetime | None = None


class EvaluationRefOut(BaseModel):
    evaluation_id: str = ""
    evaluation_version: str | None = None
    registered_at: datetime | None = None


class ExternalTrainingReferenceOut(BaseModel):
    external_ref_id: UUID
    project_id: UUID
    adapter_id: str
    adapter_version: str
    training_delivery_id: UUID
    training_request_revision_id: UUID
    dataset_version_id: UUID
    external_run_id: str
    external_run_url: str | None = None
    external_status: str
    status_stale: bool = False
    submitted_at: datetime
    last_synced_at: datetime
    model_ref: ModelRefOut | None = None
    evaluation_ref: EvaluationRefOut | None = None
    correlation_id: str
    idempotency_key: str
    error_code: str | None = None
    error_summary: str | None = None
    allowed_actions: list[str] = []
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "DatasetGrantOut",
    "DatasetItemOut",
    "DatasetOut",
    "DatasetVersionOut",
    "DedupPolicyOut",
    "ExportArtifactDownloadOut",
    "ExportArtifactManifestOut",
    "ExportJobOut",
    "ExternalTrainingReferenceOut",
    "LeakagePolicyOut",
    "LeakageReportOut",
    "ManifestOut",
    "PolicyRefOut",
    "QualityPolicyOut",
    "QualityReportOut",
    "SelectionPreviewOut",
    "SplitPolicyOut",
    "TrainingDeliveryOut",
    "TrainingRequestOut",
    "TrainingRequestRevisionOut",
]
