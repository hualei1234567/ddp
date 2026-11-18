/** 阶段 5 模型闭环领域类型；字段以契约草案与运行期响应为准。 */

export interface Model {
  model_id: string;
  project_id: string;
  code: string;
  name: string;
  description: string | null;
  task_type: string;
  framework: string;
  owner_id: string;
  status: string;
  current_version_id: string | null;
  tags: string[];
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ModelArtifact {
  object_key: string;
  sha256: string;
  size_bytes: number;
  format: string;
  verification_status: string;
}

export interface ModelRuntime {
  framework: string;
  framework_version: string;
  python_version: string | null;
  cuda_version: string | null;
  image_ref: string | null;
}

export interface ModelVersion {
  model_version_id: string;
  model_id: string;
  project_id: string;
  version_no: number;
  status: string;
  artifact: ModelArtifact;
  runtime: ModelRuntime;
  training_request_id: string | null;
  training_delivery_id: string | null;
  external_ref_id: string | null;
  dataset_version_ids: string[];
  metrics_summary: Record<string, number>;
  release_notes: string | null;
  validated_at: string | null;
  released_at: string | null;
  deprecated_at: string | null;
  deprecation_reason: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export type FailureCaseCategory =
  | "PERCEPTION"
  | "PLANNING"
  | "CONTROL"
  | "LANGUAGE"
  | "OTHER";

export type FailureCaseSeverity = "BLOCKER" | "MAJOR" | "MINOR";

/** 评测失败用例：平台只记录事实，不在此推断 Data Gap。 */
export interface FailureCase {
  id: string;
  category: FailureCaseCategory;
  severity: FailureCaseSeverity;
  episode_key: string | null;
  episode_id: string | null;
  evidence: Record<string, unknown> | string | null;
  metric: string | null;
  observed: number | string | null;
  expected: number | string | null;
  suggested_gap: string | null;
}

export interface Evaluation {
  evaluation_id: string;
  project_id: string;
  model_version_id: string;
  dataset_version_id: string;
  baseline_evaluation_id: string | null;
  status: string;
  metrics: Record<string, number>;
  thresholds: Record<string, { min?: number | null; max?: number | null }>;
  verdict: string;
  report_ref: Record<string, unknown> | null;
  failure_cases: FailureCase[];
  error_code: string | null;
  error_summary: string | null;
  started_at: string | null;
  finished_at: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InferenceService {
  service_id: string;
  project_id: string;
  model_version_id: string;
  environment: string;
  endpoint_url: string;
  resource_ref: string | null;
  config: Record<string, unknown>;
  status: string;
  health_status: string;
  deployed_at: string | null;
  stopped_at: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InferenceTask {
  task_id: string;
  project_id: string;
  model_version_id: string;
  service_id: string | null;
  dataset_version_id: string | null;
  episode_ids: string[];
  status: string;
  result_ref: Record<string, unknown> | null;
  metrics: Record<string, number>;
  log_ref: string | null;
  error_code: string | null;
  error_summary: string | null;
  started_at: string | null;
  finished_at: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface GapDimension {
  dimension_type: string;
  dimension_value: string;
  current_count: number | null;
  target_count: number | null;
}

export interface ModelFeedback {
  feedback_id: string;
  project_id: string;
  model_version_id: string;
  evaluation_id: string | null;
  inference_task_id: string | null;
  issue_type: string;
  severity: string;
  description: string;
  dimensions: GapDimension[];
  evidence_refs: string[];
  status: string;
  converted_requirement_id: string | null;
  resolution_note: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DataRequirement {
  requirement_id: string;
  project_id: string;
  code: string;
  title: string;
  description: string | null;
  source_type: string;
  source_feedback_ids: string[];
  source_evaluation_ids: string[];
  dimensions: GapDimension[];
  target_episode_count: number;
  quality_thresholds: Record<string, unknown>;
  priority: string;
  due_at: string | null;
  owner_id: string;
  status: string;
  linked_dataset_version_ids: string[];
  retest_evaluation_ids: string[];
  satisfied_at: string | null;
  satisfaction_evidence_ref: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
}

export interface GapSummaryItem {
  dimension_type: string;
  dimension_value: string;
  open_feedback_count: number;
  current_count: number | null;
  target_count: number | null;
  suggested_episode_count: number;
  requirement_ids: string[];
}

export interface GapSummary {
  items: GapSummaryItem[];
  freshness: string;
}

export const MODEL_STATUS_VALUES = ["DRAFT", "ACTIVE", "DEPRECATED", "ARCHIVED"] as const;
export const MODEL_TASK_TYPE_VALUES = ["IMITATION", "REINFORCEMENT", "VLM", "OTHER"] as const;
export const MODEL_VERSION_STATUS_VALUES = [
  "DRAFT",
  "VALIDATING",
  "VALIDATION_FAILED",
  "READY",
  "RELEASED",
  "DEPRECATED",
  "ARCHIVED",
] as const;
export const EVALUATION_STATUS_VALUES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
] as const;
export const EVALUATION_VERDICT_VALUES = ["PASS", "FAIL", "UNKNOWN"] as const;
export const FEEDBACK_STATUS_VALUES = [
  "OPEN",
  "TRIAGED",
  "REQUIREMENT_CREATED",
  "RESOLVED",
  "DISMISSED",
] as const;
export const FEEDBACK_ISSUE_TYPE_VALUES = [
  "ACTION_COVERAGE",
  "SCENE_COVERAGE",
  "LONG_TAIL",
  "VIEWPOINT",
  "GENERALIZATION",
  "LABEL_GAP",
  "DATA_QUALITY",
  "OTHER",
] as const;
export const FEEDBACK_SEVERITY_VALUES = ["BLOCKER", "MAJOR", "MINOR"] as const;
export const GAP_DIMENSION_VALUES = [
  "SCENE",
  "ACTION",
  "ROBOT",
  "DEVICE",
  "LABEL",
  "MODALITY",
] as const;
export const REQUIREMENT_STATUS_VALUES = [
  "DRAFT",
  "SUBMITTED",
  "APPROVED",
  "PLANNED",
  "IN_PROGRESS",
  "SATISFIED",
  "CLOSED",
  "REJECTED",
  "CANCELLED",
] as const;
export const INFERENCE_SERVICE_STATUS_VALUES = [
  "PENDING",
  "DEPLOYING",
  "ACTIVE",
  "STOPPED",
  "FAILED",
  "RETIRED",
] as const;
export const INFERENCE_TASK_STATUS_VALUES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
] as const;
