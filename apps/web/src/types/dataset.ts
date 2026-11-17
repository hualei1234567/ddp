/** 阶段 4 数据集与训练支撑领域类型；字段以 packages/contracts/schemas/*.json 为准。 */

export interface Dataset {
  dataset_id: string;
  project_id: string;
  dataset_lineage_id: string;
  code: string;
  name: string;
  description: string | null;
  purpose: string;
  owner_id: string;
  status: string;
  current_version_id: string | null;
  tags: string[];
  usage_policy: string;
  frozen_version_count: number;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PolicyRef {
  policy_id: string;
  version: number;
  policy_sha256: string;
}

export interface DatasetVersion {
  dataset_version_id: string;
  project_id: string;
  dataset_id: string;
  dataset_lineage_id: string;
  dataset_version: number;
  status: string;
  validity_status: string;
  selection_rule_version: string;
  selection_filters: Record<string, unknown>;
  dedup_policy_ref: PolicyRef | null;
  split_policy_ref: PolicyRef | null;
  leakage_policy_ref: PolicyRef | null;
  quality_policy_ref: PolicyRef | null;
  candidate_count: number;
  selected_count: number;
  excluded_count: number;
  duplicated_count: number;
  item_count: number;
  train_count: number;
  validation_count: number;
  test_count: number;
  total_duration_ns: number | null;
  total_bytes: number | null;
  manifest_key: string | null;
  manifest_sha256: string | null;
  leakage_status: string | null;
  build_attempt: number;
  frozen_by: string | null;
  frozen_at: string | null;
  supersedes_version_id: string | null;
  blocking_issues: string[];
  warnings: string[];
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DatasetItem {
  dataset_item_id: string;
  project_id: string;
  dataset_version_id: string;
  episode_id: string;
  episode_version: number;
  episode_lineage_id: string;
  split: string;
  sample_role: string;
  weight: number;
  selected_reason: string | null;
  item_sha256: string;
  created_at: string;
}

export interface PolicyBase {
  project_id: string;
  dataset_id: string;
  name: string | null;
  version: number;
  policy_sha256: string;
  created_at: string;
}

export interface DedupPolicy extends PolicyBase {
  dedup_policy_id: string;
  exact_keys: string[];
  near_duplicate_enabled: boolean;
  near_duplicate_enforcement: string;
  retain_rule: string;
}

export interface SplitPolicy extends PolicyBase {
  split_policy_id: string;
  strategy: string;
  train_ratio: number;
  validation_ratio: number;
  test_ratio: number;
  seed: number;
  group_by: string[];
  empty_split_policy: string;
}

export interface LeakagePolicy extends PolicyBase {
  leakage_policy_id: string;
  hard_rules: string[];
  soft_rules: string[];
  waiver_allowed_rules: string[];
}

export interface QualityPolicy extends PolicyBase {
  quality_policy_id: string;
  min_selected_count: number;
  allow_warn_quality: boolean;
  require_leakage_pass: boolean;
}

export interface SelectionPreview {
  candidate_count: number;
  selected_count: number;
  excluded_count: number;
  duplicated_count: number;
  split_counts: Record<string, number>;
  split_preview_status: string;
  exclusion_summary: Record<string, number>;
  warnings: string[];
  blocking_issues: string[];
  preview_sha256: string;
}

export interface QualityReport {
  quality_report_id: string;
  status: string;
  candidate_count: number;
  selected_count: number;
  excluded_count: number;
  duplicated_count: number;
  split_counts: Record<string, number>;
  warnings: string[];
  blocking_issues: string[];
  report_sha256: string;
  created_at: string;
}

export interface LeakageViolation {
  rule: string;
  severity: string;
  item_id: string | null;
  detail: string | null;
}

export interface LeakageReport {
  leakage_report_id: string;
  leakage_status: string;
  checked_rule_count: number;
  violation_count: number;
  hard_violation_count: number;
  soft_violation_count: number;
  violations: LeakageViolation[];
  waivers: Record<string, unknown>[];
  unknown_reason: string | null;
  report_sha256: string;
  checked_at: string;
}

export interface DatasetManifest {
  dataset_version_id: string;
  dataset_version: number;
  project_id: string;
  split_counts: Record<string, number>;
  items: Record<string, unknown>[];
  quality_report_sha256: string;
  leakage_report_sha256: string;
  manifest_sha256: string;
  size_bytes: number | null;
}

export interface DatasetGrant {
  grant_id: string;
  project_id: string;
  dataset_id: string;
  dataset_version_id: string | null;
  grantee_project_id: string;
  grantee_team: string | null;
  scope: string[];
  status: string;
  reason: string;
  approved_at: string | null;
  expires_at: string;
  revoked_at: string | null;
  allowed_actions: string[];
  version: number;
}

export interface ExportJob {
  export_job_id: string;
  project_id: string;
  dataset_version_id: string;
  status: string;
  format: string;
  format_version: string;
  exporter_id: string;
  exporter_version: string;
  mapping_version: string;
  output_prefix: string;
  artifact_count: number;
  total_bytes: number;
  aggregate_sha256: string | null;
  attempt: number;
  max_attempts: number;
  verification_status: string;
  error_code: string | null;
  error_summary: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ExportArtifact {
  path: string;
  sha256: string;
  size_bytes: number;
}

export interface ExportArtifactManifest {
  export_job_id: string;
  aggregate_sha256: string | null;
  artifacts: ExportArtifact[];
}

export interface ExportArtifactDownload {
  path: string;
  sha256: string;
  size_bytes: number;
  download_url: string;
  download_url_expires_at: string;
}

export interface RequirementSnapshot {
  business_goal: string;
  model_type: string;
  training_type: string;
  data_modalities: string[];
  scene_targets: string[];
  action_targets: string[];
  robot_targets: string[];
  device_targets: string[];
  required_labels: string[];
  required_schema_versions: string[];
  target_episode_count: number;
  quality_thresholds: Record<string, unknown>;
  split_requirements: Record<string, unknown>;
  format: string;
  format_version: string;
  delivery_due_at: string;
  acceptance_method: string;
  priority: string;
}

export interface TrainingRequest {
  training_request_id: string;
  project_id: string;
  code: string;
  title: string;
  status: string;
  current_revision_id: string | null;
  current_revision_no: number | null;
  priority: string;
  owner_id: string;
  blocked_reason: string | null;
  cancel_reason: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TrainingRequestRevision {
  revision_id: string;
  training_request_id: string;
  project_id: string;
  revision_no: number;
  status: string;
  snapshot: Record<string, unknown>;
  change_reason: string | null;
  approved_at: string | null;
  rejection_reason: string | null;
  revision_sha256: string;
  allowed_actions: string[];
  version: number;
  created_at: string;
}

export interface TrainingDelivery {
  training_delivery_id: string;
  project_id: string;
  training_request_id: string;
  training_request_revision_id: string;
  dataset_version_id: string;
  export_job_id: string;
  delivery_note_no: string;
  recipient_id: string;
  recipient_team: string | null;
  artifact_manifest_sha256: string;
  aggregate_sha256: string;
  item_count: number;
  split_counts: Record<string, number>;
  quality_report_ref: string | null;
  status: string;
  delivered_at: string | null;
  accepted_at: string | null;
  acceptance_opinion: string | null;
  rejection_reason: string | null;
  rework_ref: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
}

export interface ExternalTrainingReference {
  external_ref_id: string;
  project_id: string;
  adapter_id: string;
  adapter_version: string;
  training_delivery_id: string;
  training_request_revision_id: string;
  dataset_version_id: string;
  external_run_id: string;
  external_run_url: string | null;
  external_status: string;
  status_stale: boolean;
  model_ref: Record<string, unknown> | null;
  evaluation_ref: Record<string, unknown> | null;
  /**
   * 训练回调回传的产物元数据（model/metadata 的 object_key、sha256、size_bytes，
   * 以及 metrics）。模型版本登记从这里取；null 表示该次训练没有产物信息。
   */
  artifacts: Record<string, unknown> | null;
  correlation_id: string;
  idempotency_key: string;
  error_code: string | null;
  error_summary: string | null;
  allowed_actions: string[];
  version: number;
  submitted_at: string;
  last_synced_at: string;
}

export const DATASET_STATUS_VALUES = ["DRAFT", "ACTIVE", "ARCHIVED"] as const;
export const DATASET_VERSION_STATUS_VALUES = [
  "DRAFT",
  "BUILDING",
  "VALIDATING",
  "READY",
  "FROZEN",
  "SUPERSEDED",
  "VALIDATION_FAILED",
  "CANCELLED",
  "ARCHIVED",
] as const;
export const EXPORT_JOB_STATUS_VALUES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "PARTIAL",
  "CANCELLED",
] as const;
export const TRAINING_REQUEST_STATUS_VALUES = [
  "DRAFT",
  "SUBMITTED",
  "PENDING_REVIEW",
  "APPROVED",
  "IN_DELIVERY",
  "DELIVERED",
  "ACCEPTED",
  "CLOSED",
  "REJECTED",
  "REVISION_REQUIRED",
  "BLOCKED",
  "CANCELLED",
] as const;
export const TRAINING_DELIVERY_STATUS_VALUES = [
  "DRAFT",
  "READY",
  "DELIVERED",
  "ACCEPTED",
  "REJECTED",
  "REWORK",
  "CANCELLED",
] as const;
export const EXTERNAL_STATUS_VALUES = [
  "SUBMITTED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "UNKNOWN",
  "UNAVAILABLE",
] as const;
export const DATASET_PURPOSE_VALUES = [
  "TRAINING",
  "EVALUATION",
  "BENCHMARK",
  "OTHER",
] as const;
export const DATASET_USAGE_POLICY_VALUES = [
  "TRAINING_ALLOWED",
  "TRAINING_DENIED",
  "UNKNOWN",
] as const;
export const DEDUP_EXACT_KEYS = [
  "EPISODE_LINEAGE",
  "MANIFEST_SHA256",
  "RAW_ASSET_SHA256",
] as const;
export const SPLIT_STRATEGIES = ["RATIO", "FIXED_COUNT"] as const;
export const GRANT_SCOPE_VALUES = ["VIEW", "DOWNLOAD", "EXPORT"] as const;
