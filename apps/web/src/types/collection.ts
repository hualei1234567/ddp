import type { CursorPage } from "./api";

/**
 * 阶段 2 采集/上传/Episode 契约类型（对应 DDP-301 草案
 * `docs/architecture/contract-draft/collection/*.schema.json`）。
 *
 * 契约状态为 DRAFT，尚未冻结；类型只描述服务端可能返回的结构，
 * 不用于推断状态流转或权限。状态枚举用于响应校验，未知值会直接拒绝。
 */

export const COLLECTION_TASK_STATUS_VALUES = [
  "DRAFT",
  "PENDING_REVIEW",
  "APPROVED",
  "READY",
  "ASSIGNED",
  "IN_PROGRESS",
  "PAUSED",
  "BLOCKED",
  "SUBMITTED",
  "DATA_CHECKING",
  "ACCEPTED",
  "REJECTED",
  "REWORK",
  "COMPLETED",
  "CANCELLED",
] as const;

export const COLLECTION_BATCH_STATUS_VALUES = [
  "PLANNED",
  "READY",
  "IN_PROGRESS",
  "PAUSED",
  "BLOCKED",
  "SUBMITTED",
  "ACCEPTED",
  "REJECTED",
  "REWORK",
  "CANCELLED",
] as const;

export const UPLOAD_SESSION_STATUS_VALUES = [
  "CREATED",
  "UPLOADING",
  "COMPLETING",
  "COMPLETED",
  "ABORTED",
  "EXPIRED",
  "FAILED",
] as const;

export const UPLOAD_VERIFICATION_STATUS_VALUES = [
  "PENDING",
  "VERIFIED",
  "REJECTED",
] as const;

export const RAW_ASSET_STATUS_VALUES = [
  "VALIDATING",
  "VALID",
  "QUARANTINED",
  "REJECTED",
  "ARCHIVED",
  "TOMBSTONED",
] as const;

export const EPISODE_STATUS_VALUES = [
  "PENDING",
  "PROCESSING",
  "READY",
  "QUALITY_CHECKING",
  "ACCEPTED",
  "COMPLETED",
  "ARCHIVED",
  "FAILED",
  "REJECTED",
  "INVALIDATED",
] as const;

export const EPISODE_QUALITY_STATUS_VALUES = [
  "NOT_CHECKED",
  "PASS",
  "WARN",
  "FAIL",
] as const;

export const EPISODE_ANNOTATION_STATUS_VALUES = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "SUBMITTED",
  "APPROVED",
  "REJECTED",
] as const;

export const ASSET_FORMAT_VALUES = [
  "MCAP",
  "ROSBAG2",
  "HDF5",
  "MP4",
  "PARQUET",
  "CSV",
  "JSON",
  "ZIP",
  "UNKNOWN",
] as const;

export const COLLECTION_PRIORITY_VALUES = ["P0", "P1", "P2"] as const;

export type CollectionTaskStatus =
  (typeof COLLECTION_TASK_STATUS_VALUES)[number];
export type CollectionBatchStatus =
  (typeof COLLECTION_BATCH_STATUS_VALUES)[number];
export type UploadSessionStatus =
  (typeof UPLOAD_SESSION_STATUS_VALUES)[number];
export type UploadVerificationStatus =
  (typeof UPLOAD_VERIFICATION_STATUS_VALUES)[number];
export type RawAssetStatus = (typeof RAW_ASSET_STATUS_VALUES)[number];
export type EpisodeStatus = (typeof EPISODE_STATUS_VALUES)[number];
export type EpisodeQualityStatus =
  (typeof EPISODE_QUALITY_STATUS_VALUES)[number];
export type EpisodeAnnotationStatus =
  (typeof EPISODE_ANNOTATION_STATUS_VALUES)[number];
export type AssetFormat = (typeof ASSET_FORMAT_VALUES)[number];
export type CollectionPriority = (typeof COLLECTION_PRIORITY_VALUES)[number];

/**
 * 任务响应。
 *
 * `allowed_actions` 不是 DDP-301 草案 schema 的必填字段，但 PRD 要求动作按钮
 * 基于服务端下发集合。若服务端后续返回该扩展字段，前端只展示；缺失时所有
 * 写操作保持禁用，绝不根据 status 自行推断可执行动作。
 */
export interface CollectionTask {
  task_id: string;
  project_id: string;
  code: string;
  name: string;
  requirement_id: string | null;
  plan_id: string | null;
  sop_id: string | null;
  sop_version: string | null;
  scenario_id: string | null;
  action_spec_id: string | null;
  action_spec_version: string | null;
  status: CollectionTaskStatus;
  priority: CollectionPriority;
  assigned_to: string | null;
  assigned_robot_id: string | null;
  assigned_device_ids: string[];
  assigned_sensor_ids: string[];
  location_id: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  target_episode_count: number;
  target_duration_sec: number;
  submitted_episode_count: number;
  accepted_episode_count: number;
  quality_summary: Record<string, unknown> | null;
  status_reason: string | null;
  blocked_reason: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
  allowed_actions?: string[];
}

export interface CollectionBatch {
  batch_id: string;
  project_id: string;
  task_id: string;
  code: string;
  sequence_no: number;
  status: CollectionBatchStatus;
  assigned_to: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  expected_count: number;
  submitted_count: number;
  accepted_count: number;
  status_reason: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions?: string[];
}

export interface UploadPartSummary {
  part_number: number;
  size_bytes: number;
  etag: string;
  sha256: string;
  recorded_at: string;
}

export interface UploadSession {
  upload_session_id: string;
  project_id: string;
  collection_task_id: string | null;
  batch_id: string | null;
  status: UploadSessionStatus;
  verification_status: UploadVerificationStatus;
  verification_error_code: string | null;
  verified_at: string | null;
  original_filename: string;
  declared_content_type: string;
  declared_size_bytes: number;
  expected_sha256: string | null;
  format_hint: AssetFormat | null;
  part_size_bytes: number;
  max_size_bytes: number;
  allowed_content_types: string[];
  object_key_prefix?: string;
  object_key: string | null;
  expires_at: string;
  completed_at: string | null;
  aborted_at: string | null;
  failure_code: string | null;
  failure_summary: string | null;
  uploaded_bytes: number;
  uploaded_part_count: number;
  missing_part_numbers: number[];
  uploaded_parts: UploadPartSummary[];
  deduplicated: boolean;
  deduplicated_asset_id: string | null;
  asset_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface UploadPartUrlResponse {
  part_number: number;
  method: "PUT";
  url: string;
  headers: Record<string, string>;
  expires_at: string;
}

export interface UploadPartAckResponse {
  part_number: number;
  etag: string;
  size_bytes: number;
  sha256: string;
  recorded_at: string;
  uploaded_bytes: number;
  uploaded_part_count: number;
}

export interface UploadPartAckRequest {
  etag: string;
  size_bytes: number;
  sha256: string;
}

export interface UploadPartList {
  items: UploadPartSummary[];
  missing_part_numbers: number[];
  uploaded_bytes: number;
}

export interface RawAsset {
  asset_id: string;
  project_id: string;
  code: string;
  source_type: "UPLOAD" | "DEVICE" | "IMPORT";
  device_time_raw: Record<string, unknown> | null;
  collection_task_id: string | null;
  batch_id: string | null;
  upload_session_id: string | null;
  original_filename: string;
  object_key: string;
  size_bytes: number;
  content_type: string;
  format: AssetFormat;
  sha256: string;
  status: RawAssetStatus;
  captured_at: string | null;
  ingested_at: string | null;
  device_ids: string[];
  sensor_ids: string[];
  sensor_snapshot: Record<string, unknown> | null;
  raw_metadata: Record<string, unknown>;
  storage_class: "STANDARD" | "INFREQUENT" | "ARCHIVE";
  retention_policy_id: string | null;
  quarantine_reason: string | null;
  quarantine_codes: string[];
  scan_report_key: string | null;
  verified_at: string | null;
  archived_at: string | null;
  tombstoned_at: string | null;
  duplicate_of_asset_id: string | null;
  uploaded_by: string;
  version: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface CompleteUploadResponse {
  upload_session: UploadSession;
  raw_asset: RawAsset | null;
  deduplicated: boolean;
  validation_job_id: string;
  replayed: boolean;
}

export interface TimeBase {
  time_domain: "UTC" | "ROS" | "DEVICE" | "HOST" | "MONOTONIC" | "UNKNOWN";
  clock_source:
    | "PTP"
    | "NTP"
    | "ROS_CLOCK"
    | "DEVICE_CLOCK"
    | "HOST_CLOCK"
    | "MONOTONIC"
    | "EXTERNAL"
    | "UNKNOWN";
  clock_id: string | null;
  offset_to_utc_ns: string | null;
  drift_ppm: number | null;
  uncertainty_ns: string | null;
  sync_method:
    | "PTP"
    | "NTP"
    | "HARDWARE_TRIGGER"
    | "SOFTWARE_ESTIMATE"
    | "MANUAL"
    | "NONE";
  sync_ref: string | null;
  sync_status: "VERIFIED" | "DEGRADED" | "UNVERIFIED" | "FAILED";
}

export interface CalibrationSnapshotItem {
  calibration_id: string;
  calibration_version: number;
  checksum: string;
  target_type: "ROBOT" | "DEVICE" | "SENSOR" | "SYSTEM";
  target_id: string;
  schema_version: string;
}

export interface EpisodePreviewRef {
  preview_id: string;
  object_key: string;
  content_type: string;
  sha256: string | null;
}

export interface Episode {
  episode_id: string;
  episode_lineage_id: string;
  episode_version: number;
  project_id: string;
  code: string;
  task_id: string | null;
  batch_id: string | null;
  robot_id: string | null;
  device_ids: string[];
  sensor_ids: string[];
  raw_asset_ids: string[];
  start_time_ns: string;
  end_time_ns: string;
  duration_ns: string;
  time_domain: "UTC";
  time_base: TimeBase;
  canonical_schema_version: string;
  pipeline_id: string;
  pipeline_version: string;
  segmentation_rule_version: string;
  status: EpisodeStatus;
  quality_status: EpisodeQualityStatus;
  annotation_status: EpisodeAnnotationStatus;
  success: boolean | null;
  failure_reason: string | null;
  scene_labels: string[];
  language_instruction: string | null;
  frame_count: number;
  calibration_snapshot: CalibrationSnapshotItem[];
  manifest_key: string;
  manifest_sha256: string;
  preview_refs: EpisodePreviewRef[];
  supersedes_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export type CollectionTaskPage = CursorPage<CollectionTask>;
export type CollectionBatchPage = CursorPage<CollectionBatch>;
export type EpisodePage = CursorPage<Episode>;
export type RawAssetPage = CursorPage<RawAsset>;

/** 与服务端 `CollectionTaskCreate` 一致；状态、版本与计数由服务端维护。 */
export interface CollectionTaskCreateInput {
  code: string;
  name: string;
  priority: CollectionPriority;
  target_episode_count: number;
  target_duration_sec: number;
  requirement_id?: string | null;
  plan_id?: string | null;
  sop_id?: string | null;
  sop_version?: string | null;
  scenario_id?: string | null;
  action_spec_id?: string | null;
  action_spec_version?: string | null;
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  location_id?: string | null;
}

export interface UploadSessionCreateInput {
  collection_task_id?: string | null;
  batch_id?: string | null;
  original_filename: string;
  declared_content_type: string;
  declared_size_bytes: number;
  expected_sha256?: string | null;
  format_hint?: AssetFormat | null;
  part_size_bytes?: number | null;
  metadata?: Record<string, unknown>;
}

export interface CompleteUploadPartInput {
  part_number: number;
  etag: string;
  size_bytes: number;
  sha256: string;
}

export interface CompleteUploadInput {
  expected_sha256: string;
  expected_size_bytes: number;
  parts: CompleteUploadPartInput[];
  metadata?: Record<string, unknown>;
}
