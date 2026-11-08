/**
 * 阶段 3 标注域前端类型。
 *
 * 字段与枚举来自后端 `annotation_catalog_models.py`、`annotation_work_models.py`、
 * `annotation_result_models.py`、`annotation_quality_models.py` 的实际响应。
 * 外部数据进入 UI 前必须由 `services/annotationParsers.ts` 严格解析；
 * 本文件不根据状态推导权限或可执行动作。
 */

export const ANNOTATION_SCHEMA_STATUS_VALUES = [
  "DRAFT",
  "PENDING_REVIEW",
  "PUBLISHED",
  "DEPRECATED",
  "REVOKED",
] as const;
export type AnnotationSchemaStatus =
  (typeof ANNOTATION_SCHEMA_STATUS_VALUES)[number];

export const ANNOTATION_LABEL_TYPE_VALUES = [
  "ENUM",
  "MULTI_ENUM",
  "BOOLEAN",
  "NUMBER",
  "STRING",
  "FREE_TEXT",
  "TIME_RANGE",
  "ACTION_SEGMENT",
  "OBJECT_TRACK",
  "JSON",
] as const;
export type AnnotationLabelType = (typeof ANNOTATION_LABEL_TYPE_VALUES)[number];

export const ANNOTATION_LEVEL_VALUES = [
  "TASK",
  "EPISODE",
  "TIME_RANGE",
  "ACTION_SEGMENT",
  "FRAME",
  "OBJECT_TRACK",
] as const;
export type AnnotationLevel = (typeof ANNOTATION_LEVEL_VALUES)[number];

export const LABEL_VISIBILITY_VALUES = [
  "ANNOTATOR",
  "REVIEWER_ONLY",
  "GOLD_ONLY",
  "SYSTEM_ONLY",
] as const;
export type LabelVisibility = (typeof LABEL_VISIBILITY_VALUES)[number];

export const SCHEMA_COMPATIBILITY_VALUES = [
  "BACKWARD_COMPATIBLE",
  "BREAKING",
] as const;

export const SAMPLE_SET_STATUS_VALUES = [
  "DRAFT",
  "PREVIEW",
  "SNAPSHOTTED",
  "CONSUMED",
  "CANCELLED",
] as const;
export type SampleSetStatus = (typeof SAMPLE_SET_STATUS_VALUES)[number];

export const SAMPLE_STRATEGY_VALUES = [
  "FILTER",
  "STRATIFIED",
  "RISK_BASED",
  "MANUAL",
] as const;

export const DEDUP_KEY_VALUES = [
  "EPISODE_LINEAGE",
  "EPISODE_VERSION",
  "NONE",
] as const;

export const SAMPLE_ROLE_VALUES = ["NORMAL", "GOLD", "IAA", "QUALITY"] as const;

export const SAMPLE_TIME_DOMAIN_VALUES = [
  "UTC",
  "ROS",
  "DEVICE",
  "HOST",
  "MONOTONIC",
  "UNKNOWN",
] as const;
export type SampleTimeDomain = (typeof SAMPLE_TIME_DOMAIN_VALUES)[number];

export const DATA_SOURCE_VALUES = [
  "REAL",
  "SYNTHETIC",
  "MIXED",
  "UNKNOWN",
] as const;
export type DataSource = (typeof DATA_SOURCE_VALUES)[number];

export const ANNOTATION_TASK_STATUS_VALUES = [
  "DRAFT",
  "PENDING_REVIEW",
  "APPROVED",
  "READY",
  "ASSIGNED",
  "IN_PROGRESS",
  "PAUSED",
  "BLOCKED",
  "SUBMITTED",
  "REVIEWING",
  "ARBITRATION",
  "ACCEPTED",
  "REJECTED",
  "REWORK",
  "COMPLETED",
  "CANCELLED",
  "ARCHIVED",
] as const;
export type AnnotationTaskStatus =
  (typeof ANNOTATION_TASK_STATUS_VALUES)[number];

export const TASK_PRIORITY_VALUES = ["P0", "P1", "P2", "P3"] as const;
export type TaskPriority = (typeof TASK_PRIORITY_VALUES)[number];

export const ASSIGNMENT_MODE_VALUES = ["MANUAL", "CLAIM", "MIXED"] as const;

export const ANNOTATION_BATCH_STATUS_VALUES = [
  "PLANNED",
  "READY",
  "ASSIGNED",
  "IN_PROGRESS",
  "PAUSED",
  "BLOCKED",
  "SUBMITTED",
  "REVIEWING",
  "ARBITRATION",
  "ACCEPTED",
  "REJECTED",
  "REWORK",
  "CANCELLED",
] as const;

export const ANNOTATION_ASSIGNMENT_STATUS_VALUES = [
  "ASSIGNED",
  "CLAIMED",
  "IN_PROGRESS",
  "SUBMITTED",
  "COMPLETED",
  "REVOKED",
  "EXPIRED",
] as const;

export const ASSIGNMENT_ROLE_VALUES = [
  "ANNOTATOR",
  "REVIEWER",
  "ARBITER",
] as const;
export type AssignmentRole = (typeof ASSIGNMENT_ROLE_VALUES)[number];

export const ANNOTATION_SUBMISSION_STATUS_VALUES = [
  "DRAFT",
  "SUBMITTED",
  "IN_REVIEW",
  "APPROVED",
  "REJECTED",
  "ESCALATED",
  "ARBITRATING",
  "RESOLVED_APPROVED",
  "RESOLVED_REJECTED",
  "RESOLVED_REWORK",
  "REWORK_REQUIRED",
  "WITHDRAWN",
  "SUPERSEDED",
] as const;
export type AnnotationSubmissionStatus =
  (typeof ANNOTATION_SUBMISSION_STATUS_VALUES)[number];

export const ANNOTATION_RECORD_STATUS_VALUES = [
  "DRAFT",
  "SUBMITTED",
  "APPROVED",
  "REJECTED",
  "SUPERSEDED",
  "INVALIDATED",
] as const;

export const LABEL_VALUE_SOURCE_VALUES = [
  "HUMAN",
  "AI_ASSISTED",
  "IMPORTED",
] as const;
export type LabelValueSource = (typeof LABEL_VALUE_SOURCE_VALUES)[number];

export const LABEL_VALUE_STATUS_VALUES = [
  "DRAFT",
  "CONFIRMED",
  "REJECTED",
] as const;
export type LabelValueStatus = (typeof LABEL_VALUE_STATUS_VALUES)[number];

export const ANNOTATION_REVIEW_STATUS_VALUES = [
  "PENDING",
  "IN_REVIEW",
  "APPROVED",
  "REJECTED",
  "ESCALATED",
  "CANCELLED",
] as const;
export type AnnotationReviewStatus =
  (typeof ANNOTATION_REVIEW_STATUS_VALUES)[number];

export const REVIEW_DECISION_VALUES = [
  "APPROVE",
  "REJECT",
  "ESCALATE",
] as const;

export const REVIEW_ISSUE_SEVERITY_VALUES = [
  "BLOCKER",
  "MAJOR",
  "MINOR",
  "SUGGESTION",
] as const;
export type ReviewIssueSeverity =
  (typeof REVIEW_ISSUE_SEVERITY_VALUES)[number];

export const ANNOTATION_ARBITRATION_STATUS_VALUES = [
  "PENDING",
  "IN_ARBITRATION",
  "RESOLVED_APPROVED",
  "RESOLVED_REJECTED",
  "RESOLVED_REWORK",
  "CANCELLED",
] as const;
export type AnnotationArbitrationStatus =
  (typeof ANNOTATION_ARBITRATION_STATUS_VALUES)[number];

export const ARBITRATION_TRIGGER_VALUES = [
  "REVIEWER_DISAGREEMENT",
  "ANNOTATOR_DISPUTE",
  "GOLD_SET_CONFLICT",
  "IAA_CONFLICT",
  "HIGH_RISK",
  "POLICY",
] as const;
export type ArbitrationTrigger = (typeof ARBITRATION_TRIGGER_VALUES)[number];

export const ARBITRATION_DECISION_VALUES = [
  "APPROVE",
  "REJECT",
  "REWORK",
  "ABSTAIN",
] as const;
export type ArbitrationDecision = (typeof ARBITRATION_DECISION_VALUES)[number];

export const ARBITRATION_FINAL_DECISION_VALUES = [
  "APPROVE",
  "REJECT",
  "REWORK",
] as const;

export const GOLD_SET_STATUS_VALUES = [
  "DRAFT",
  "PENDING_REVIEW",
  "ACTIVE",
  "RETIRED",
  "REVOKED",
] as const;
export type GoldSetStatus = (typeof GOLD_SET_STATUS_VALUES)[number];

export const GOLD_EXPOSURE_POLICY_VALUES = [
  "HIDDEN",
  "ROTATING",
  "CONTROLLED",
] as const;

export const GOLD_ROTATION_POLICY_VALUES = [
  "NONE",
  "PER_TASK",
  "PER_PERIOD",
  "PER_ANNOTATOR",
] as const;

export const GOLD_ITEM_DIFFICULTY_VALUES = [
  "EASY",
  "MEDIUM",
  "HARD",
] as const;
export type GoldItemDifficulty =
  (typeof GOLD_ITEM_DIFFICULTY_VALUES)[number];

export const QUALITY_SAMPLE_STATUS_VALUES = [
  "PLANNED",
  "SAMPLED",
  "ASSIGNED",
  "IN_REVIEW",
  "PASSED",
  "FAILED",
  "ESCALATED",
  "CLOSED",
  "CANCELLED",
] as const;
export type QualitySampleStatus =
  (typeof QUALITY_SAMPLE_STATUS_VALUES)[number];

export const QUALITY_SAMPLE_TYPE_VALUES = [
  "RANDOM",
  "STRATIFIED",
  "RISK_BASED",
  "GOLD_SET",
  "IAA_OVERLAP",
] as const;
export type QualitySampleType = (typeof QUALITY_SAMPLE_TYPE_VALUES)[number];

export const QUALITY_SAMPLE_OUTCOME_VALUES = [
  "PASS",
  "FAIL",
  "ESCALATE",
] as const;
export type QualitySampleOutcome =
  (typeof QUALITY_SAMPLE_OUTCOME_VALUES)[number];

export const IAA_REPORT_STATUS_VALUES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "INSUFFICIENT_DATA",
  "CANCELLED",
] as const;
export type IAAReportStatus = (typeof IAA_REPORT_STATUS_VALUES)[number];

export const IAA_METRIC_VALUES = [
  "PERCENT_AGREEMENT",
  "COHEN_KAPPA",
  "FLEISS_KAPPA",
  "KRIPPENDORFF_ALPHA",
  "SPAN_F1",
  "BOUNDARY_MAE",
  "GOLD_ACCURACY",
] as const;
export type IAAMetric = (typeof IAA_METRIC_VALUES)[number];

export const IAA_VALUE_STATUS_VALUES = [
  "OK",
  "N/A",
  "INSUFFICIENT_DATA",
  "PARTIAL",
] as const;
export type IAAValueStatus = (typeof IAA_VALUE_STATUS_VALUES)[number];

export interface LabelDefinition {
  label_key: string;
  label_type: AnnotationLabelType;
  required: boolean;
  level: AnnotationLevel;
  visibility: LabelVisibility;
  description: string | null;
  /** 允许扩展字段；表单只读取已定义的已知键。 */
  value_spec: Record<string, unknown>;
  quality_rules: string[];
}

export interface AnnotationSchema {
  schema_id: string;
  project_id: string;
  code: string;
  name: string;
  description: string | null;
  schema_lineage_id: string;
  schema_version: number;
  status: AnnotationSchemaStatus;
  annotation_levels: AnnotationLevel[];
  labels: LabelDefinition[];
  compatibility: string;
  schema_sha256: string;
  supersedes_id: string | null;
  published_at: string | null;
  deprecated_at: string | null;
  revoked_at: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface SampleEpisodeRef {
  episode_id: string;
  episode_lineage_id: string;
  episode_version: number;
  sample_role: string;
  selected_reason: string;
  stratum: string | null;
  time_domain: SampleTimeDomain;
  start_time_ns: number | null;
  end_time_ns: number | null;
}

export interface AnnotationSampleSet {
  sample_set_id: string;
  project_id: string;
  code: string;
  name: string | null;
  status: SampleSetStatus;
  selection_rule_version: string;
  strategy: string;
  filters: Record<string, unknown>;
  strata: Record<string, unknown>[];
  episode_refs: SampleEpisodeRef[];
  dedup_key: string;
  candidate_count: number;
  selected_count: number;
  excluded_count: number;
  deduplicated_count: number;
  snapshot_sha256: string | null;
  snapshot_at: string | null;
  cancelled_reason: string | null;
  data_source: DataSource;
  exclusion_summary: Record<string, unknown>;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface AnnotationTask {
  task_id: string;
  project_id: string;
  code: string;
  name: string;
  description: string | null;
  sample_set_id: string;
  schema_id: string;
  schema_version: number;
  schema_sha256: string;
  status: AnnotationTaskStatus;
  target_item_count: number;
  target_episode_count: number;
  priority: TaskPriority;
  assignment_mode: string;
  iaa_policy: Record<string, unknown>;
  quality_policy: Record<string, unknown>;
  progress: Record<string, unknown>;
  quality_summary: Record<string, unknown>;
  due_at: string | null;
  accepted_at: string | null;
  completed_at: string | null;
  gold_set_id: string | null;
  iaa_report_id: string | null;
  status_reason: string | null;
  blocked_reason: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface AnnotationBatch {
  batch_id: string;
  project_id: string;
  task_id: string;
  code: string;
  sequence_no: number;
  status: string;
  name: string | null;
  stratum: string | null;
  item_refs: string[];
  planned_item_count: number;
  assignee_ids: string[];
  reviewer_pool_ids: string[];
  due_at: string | null;
  submitted_item_count: number;
  approved_item_count: number;
  rejected_item_count: number;
  rework_count: number;
  quality_summary: Record<string, unknown>;
  status_reason: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface AnnotationAssignment {
  assignment_id: string;
  project_id: string;
  task_id: string;
  batch_id: string;
  assignee_id: string;
  status: string;
  assignment_role: AssignmentRole;
  planned_item_count: number;
  claimed_item_count: number;
  submitted_item_count: number;
  approved_item_count: number;
  planned_minutes: number;
  actual_minutes: number | null;
  lease_expires_at: string | null;
  claimed_at: string | null;
  submitted_at: string | null;
  completed_at: string | null;
  revoked_reason: string | null;
  status_reason: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface LabelValue {
  label_key: string;
  value: unknown;
  source: LabelValueSource;
  label_status: LabelValueStatus;
  confidence?: number;
  comment?: string | null;
}

export interface EvidenceRef {
  evidence_id: string;
  object_key: string;
  sha256: string;
  content_type?: string | null;
}

export interface AnnotationRecord {
  annotation_id: string;
  annotation_lineage_id: string;
  annotation_version: number;
  project_id: string;
  submission_id: string;
  episode_id: string;
  episode_version: number;
  task_id: string;
  schema_id: string;
  schema_version: number;
  status: string;
  time_range: Record<string, unknown> | null;
  labels: LabelValue[];
  evidence_refs: EvidenceRef[];
  record_sha256: string;
  supersedes_id: string | null;
  approved_by: string | null;
  approved_at: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface AnnotationSubmission {
  submission_id: string;
  project_id: string;
  task_id: string;
  batch_id: string;
  assignment_id: string;
  annotator_id: string;
  submission_version: number;
  schema_id: string;
  schema_version: number;
  schema_sha256: string;
  records_digest: string;
  item_count: number;
  status: AnnotationSubmissionStatus;
  data_source: DataSource;
  record_refs: string[];
  records: AnnotationRecord[];
  review_id: string | null;
  arbitration_id: string | null;
  supersedes_id: string | null;
  submitted_at: string | null;
  decided_at: string | null;
  reason: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface ReviewIssue {
  code: string;
  severity: ReviewIssueSeverity;
  message: string;
  label_key: string | null;
  item_ref: string | null;
  evidence_ref: string | null;
}

export interface ReviewChecklistItem {
  key: string;
  passed: boolean;
  comment: string | null;
}

export interface AnnotationReview {
  review_id: string;
  project_id: string;
  submission_id: string;
  task_id: string;
  reviewer_id: string;
  review_round: number;
  status: AnnotationReviewStatus;
  decision: string | null;
  issues: ReviewIssue[];
  checklist: ReviewChecklistItem[];
  review_duration_sec: number | null;
  reason: string | null;
  decided_at: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface ArbitrationVote {
  arbiter_id: string;
  decision: ArbitrationDecision;
  reason: string | null;
  voted_at: string;
}

export interface AnnotationArbitration {
  arbitration_id: string;
  project_id: string;
  task_id: string;
  submission_id: string;
  review_id: string | null;
  arbiter_ids: string[];
  quorum: number;
  trigger: ArbitrationTrigger;
  status: AnnotationArbitrationStatus;
  votes: ArbitrationVote[];
  effective_vote_count: number;
  final_decision: string | null;
  reason: string | null;
  resolved_at: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface GoldExpectedLabel {
  label_key: string;
  value: unknown;
  tolerance?: Record<string, unknown>;
}

export interface GoldSetItem {
  gold_item_id: string;
  episode_id: string;
  difficulty: GoldItemDifficulty;
  weight: number;
  active: boolean;
  answers_redacted: boolean;
  episode_version?: number;
  time_range?: Record<string, unknown>;
  expected_labels?: GoldExpectedLabel[];
}

export interface GoldSet {
  gold_set_id: string;
  project_id: string;
  code: string;
  name: string | null;
  status: GoldSetStatus;
  schema_id: string;
  schema_version: number;
  schema_sha256: string;
  blind: boolean;
  exposure_policy: string;
  max_exposure_per_annotator: number;
  rotation_policy: string;
  items: GoldSetItem[];
  version: number;
}

export interface QualitySample {
  quality_sample_id: string;
  project_id: string;
  task_id: string;
  submission_id: string;
  annotator_id: string;
  sampling_policy_version: string;
  sample_type: QualitySampleType;
  sample_reason: string;
  status: QualitySampleStatus;
  stratum: string | null;
  item_refs: string[];
  reviewer_id: string | null;
  review_id: string | null;
  arbitration_id: string | null;
  due_at: string | null;
  outcome: QualitySampleOutcome | null;
  reason: string | null;
  closed_at: string | null;
  metadata: Record<string, unknown>;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface IAAMetricResult {
  metric: IAAMetric;
  label_key: string;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  sample_size: number;
  status: IAAValueStatus;
  unit?: string;
  config?: Record<string, unknown>;
}

export interface IAAReport {
  report_id: string;
  project_id: string;
  task_id: string;
  schema_id: string;
  schema_version: number;
  status: IAAReportStatus;
  metric_definitions_version: string;
  sample_item_count: number;
  overlap_item_count: number;
  annotator_count: number;
  metrics: IAAMetricResult[];
  excluded_items: Record<string, unknown>[];
  computed_at: string | null;
  version: number;
}

export interface AnnotationTaskCreateInput {
  code: string;
  name: string;
  description?: string | null;
  sample_set_id: string;
  schema_id: string;
  schema_version?: number | null;
  target_item_count: number;
  target_episode_count: number;
  due_at?: string | null;
  priority?: TaskPriority;
  assignment_mode?: string;
  iaa_policy?: Record<string, unknown>;
  quality_policy?: Record<string, unknown>;
}

export interface AnnotationWorkRecordInput {
  episode_id: string;
  episode_version: number;
  annotation_lineage_id?: string;
  annotation_version?: number;
  time_range?: Record<string, unknown> | null;
  labels: LabelValue[];
  evidence_refs?: EvidenceRef[];
  supersedes_id?: string | null;
}

export interface AnnotationSubmissionCreateInput {
  assignment_id: string;
  records: AnnotationWorkRecordInput[];
  data_source: DataSource;
}
