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

/** 服务端 Episode 动作白名单（对应 `EpisodeAction` Literal 与勘误 5）。 */
export const EPISODE_ACTION_VALUES = [
  "ready",
  "quality-check",
  "quality-result",
  "accept",
  "reject",
  "complete",
  "archive",
  "invalidate",
] as const;

export const LINEAGE_NODE_TYPE_VALUES = [
  "RAW_ASSET",
  "UPLOAD_SESSION",
  "COLLECTION_TASK",
  "COLLECTION_BATCH",
  "INGESTION_JOB",
  "EPISODE",
  "QUALITY_RUN",
  "ANNOTATION",
  "DATASET_VERSION",
  "TRAINING_RUN",
  // 数据源与导入任务：只读投影节点，用于回答「这条数据最初从哪来」。
  "DATA_SOURCE",
  "DATA_SOURCE_IMPORT",
] as const;

export const LINEAGE_RELATION_VALUES = [
  "UPLOADED_BY",
  "PRODUCED_BY",
  "USED_AS_INPUT",
  "PRODUCED",
  "SEGMENTED_INTO",
  "SUPERSEDES",
  "INVALIDATES",
  "EVALUATED_BY",
  "ANNOTATED_BY",
  "INCLUDED_IN",
  "TRAINED_ON",
] as const;

export const LINEAGE_DIRECTION_VALUES = ["forward", "reverse"] as const;

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
export type EpisodeAction = (typeof EPISODE_ACTION_VALUES)[number];
export type LineageNodeType = (typeof LINEAGE_NODE_TYPE_VALUES)[number];
export type LineageRelation = (typeof LINEAGE_RELATION_VALUES)[number];
export type LineageDirection = (typeof LINEAGE_DIRECTION_VALUES)[number];

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
  /** 服务端允许为 null；展示层不得把 null 当成时间 0。 */
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
  /** 服务端按状态下发；前端不得自行推断可执行动作。 */
  allowed_actions: string[];
}

export const RAW_ASSET_ACTION_VALUES = [
  "release-quarantine-or-revalidate",
  "reject-quarantined",
  "post-hoc-quarantine",
  "archive",
  "tombstone",
] as const;

export type RawAssetAction = (typeof RAW_ASSET_ACTION_VALUES)[number];

/** 与服务端 `RawAssetQuarantineCode` 保持一致；未知码由服务端拒绝。 */
export const RAW_ASSET_QUARANTINE_CODE_VALUES = [
  "malware_suspected",
  "malware_scanner_unavailable",
  "signature_ambiguous",
  "unsupported_format",
  "metadata_conflict",
  "parse_failed",
  "hash_mismatch",
  "size_mismatch",
  "object_missing",
] as const;

export type RawAssetQuarantineCode =
  (typeof RAW_ASSET_QUARANTINE_CODE_VALUES)[number];

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
  /** READY 之前服务端可能尚未生成 Manifest。 */
  manifest_key: string | null;
  manifest_sha256: string | null;
  preview_refs: EpisodePreviewRef[];
  supersedes_id: string | null;
  /** 服务端当前处理尝试；真后端会返回，其他环境可能缺失。 */
  attempt?: number;
  max_attempts?: number;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface LineageEdge {
  edge_id: string;
  project_id: string;
  from_node_type: LineageNodeType;
  from_node_id: string;
  to_node_type: LineageNodeType;
  to_node_id: string;
  relation: LineageRelation;
  metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export interface LineageResponse {
  items: LineageEdge[];
  direction: LineageDirection;
}

/** 服务端签发的短期下载地址；前端只在内存中短暂持有。 */
export interface DownloadUrl {
  url: string;
  expires_at: string;
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

/** 与服务端 `EpisodeActionRequest` 一致；未使用的字段不下发。 */
export interface EpisodeActionInput {
  reason?: string | null;
  quality_status?: EpisodeQualityStatus;
  manifest?: Record<string, unknown>;
  frame_count?: number;
}

/** 与服务端 `EpisodeRevisionCreate` 一致。 */
export interface EpisodeRevisionInput {
  code: string;
}
