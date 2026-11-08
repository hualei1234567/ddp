import type {
  CalibrationSnapshotItem,
  CollectionBatch,
  CollectionBatchPage,
  CollectionTask,
  CollectionTaskPage,
  Episode,
  EpisodePage,
  EpisodePreviewRef,
  TimeBase,
} from "../types/collection";
import {
  COLLECTION_BATCH_STATUS_VALUES,
  COLLECTION_PRIORITY_VALUES,
  COLLECTION_TASK_STATUS_VALUES,
  EPISODE_ANNOTATION_STATUS_VALUES,
  EPISODE_QUALITY_STATUS_VALUES,
  EPISODE_STATUS_VALUES,
} from "../types/collection";
import {
  asRecord,
  parseCursorPage,
  readEnum,
  readInteger,
  readOptionalBoolean,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readOptionalStringArray,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readUuid,
  readUuidArray,
  ContractValidationError,
} from "./validation";

function readUuidArrayOrEmpty(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string[] {
  if (record[key] === undefined || record[key] === null) {
    return [];
  }
  return readUuidArray(record, key, path);
}

/** 服务端扩展字段：缺失时返回 undefined，不根据状态自行推断动作。 */
function readOptionalActions(
  record: Record<string, unknown>,
  path: string,
): string[] | undefined {
  const actions = readOptionalStringArray(record, "allowed_actions", path);
  return actions ?? undefined;
}

export function parseCollectionTask(
  value: unknown,
  path = "采集任务",
): CollectionTask {
  const record = asRecord(value, path);
  return {
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    requirement_id: readOptionalUuid(
      record,
      "requirement_id",
      `${path}.requirement_id`,
    ),
    plan_id: readOptionalUuid(record, "plan_id", `${path}.plan_id`),
    sop_id: readOptionalUuid(record, "sop_id", `${path}.sop_id`),
    sop_version: readOptionalString(
      record,
      "sop_version",
      `${path}.sop_version`,
    ),
    scenario_id: readOptionalUuid(
      record,
      "scenario_id",
      `${path}.scenario_id`,
    ),
    action_spec_id: readOptionalUuid(
      record,
      "action_spec_id",
      `${path}.action_spec_id`,
    ),
    action_spec_version: readOptionalString(
      record,
      "action_spec_version",
      `${path}.action_spec_version`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      COLLECTION_TASK_STATUS_VALUES,
    ),
    priority: readEnum(
      record,
      "priority",
      `${path}.priority`,
      COLLECTION_PRIORITY_VALUES,
    ),
    assigned_to: readOptionalUuid(
      record,
      "assigned_to",
      `${path}.assigned_to`,
    ),
    assigned_robot_id: readOptionalUuid(
      record,
      "assigned_robot_id",
      `${path}.assigned_robot_id`,
    ),
    assigned_device_ids: readUuidArrayOrEmpty(
      record,
      "assigned_device_ids",
      `${path}.assigned_device_ids`,
    ),
    assigned_sensor_ids: readUuidArrayOrEmpty(
      record,
      "assigned_sensor_ids",
      `${path}.assigned_sensor_ids`,
    ),
    location_id: readOptionalUuid(
      record,
      "location_id",
      `${path}.location_id`,
    ),
    scheduled_start: readOptionalString(
      record,
      "scheduled_start",
      `${path}.scheduled_start`,
    ),
    scheduled_end: readOptionalString(
      record,
      "scheduled_end",
      `${path}.scheduled_end`,
    ),
    actual_start: readOptionalString(
      record,
      "actual_start",
      `${path}.actual_start`,
    ),
    actual_end: readOptionalString(
      record,
      "actual_end",
      `${path}.actual_end`,
    ),
    target_episode_count: readInteger(
      record,
      "target_episode_count",
      `${path}.target_episode_count`,
    ),
    target_duration_sec: readInteger(
      record,
      "target_duration_sec",
      `${path}.target_duration_sec`,
    ),
    submitted_episode_count: readInteger(
      record,
      "submitted_episode_count",
      `${path}.submitted_episode_count`,
    ),
    accepted_episode_count: readInteger(
      record,
      "accepted_episode_count",
      `${path}.accepted_episode_count`,
    ),
    quality_summary: readOptionalRecord(
      record,
      "quality_summary",
      `${path}.quality_summary`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    blocked_reason: readOptionalString(
      record,
      "blocked_reason",
      `${path}.blocked_reason`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readString(record, "created_at", `${path}.created_at`),
    updated_at: readString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
    allowed_actions: readOptionalActions(record, `${path}.allowed_actions`),
  };
}

export function parseCollectionTaskPage(value: unknown): CollectionTaskPage {
  return parseCursorPage(value, "采集任务列表", parseCollectionTask);
}

export function parseCollectionBatch(
  value: unknown,
  path = "采集批次",
): CollectionBatch {
  const record = asRecord(value, path);
  return {
    batch_id: readUuid(record, "batch_id", `${path}.batch_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    task_id: readUuid(record, "task_id", `${path}.task_id`),
    code: readString(record, "code", `${path}.code`),
    sequence_no: readPositiveInteger(
      record,
      "sequence_no",
      `${path}.sequence_no`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      COLLECTION_BATCH_STATUS_VALUES,
    ),
    assigned_to: readOptionalUuid(
      record,
      "assigned_to",
      `${path}.assigned_to`,
    ),
    scheduled_start: readOptionalString(
      record,
      "scheduled_start",
      `${path}.scheduled_start`,
    ),
    scheduled_end: readOptionalString(
      record,
      "scheduled_end",
      `${path}.scheduled_end`,
    ),
    expected_count: readInteger(
      record,
      "expected_count",
      `${path}.expected_count`,
    ),
    submitted_count: readInteger(
      record,
      "submitted_count",
      `${path}.submitted_count`,
    ),
    accepted_count: readInteger(
      record,
      "accepted_count",
      `${path}.accepted_count`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readString(record, "created_at", `${path}.created_at`),
    updated_at: readString(record, "updated_at", `${path}.updated_at`),
    allowed_actions: readOptionalActions(record, `${path}.allowed_actions`),
  };
}

export function parseCollectionBatchPage(value: unknown): CollectionBatchPage {
  return parseCursorPage(value, "采集批次列表", parseCollectionBatch);
}

function parseTimeBase(value: unknown, path: string): TimeBase {
  const record = asRecord(value, path);
  return {
    time_domain: readEnum(record, "time_domain", `${path}.time_domain`, [
      "UTC",
      "ROS",
      "DEVICE",
      "HOST",
      "MONOTONIC",
      "UNKNOWN",
    ] as const),
    clock_source: readEnum(record, "clock_source", `${path}.clock_source`, [
      "PTP",
      "NTP",
      "ROS_CLOCK",
      "DEVICE_CLOCK",
      "HOST_CLOCK",
      "MONOTONIC",
      "EXTERNAL",
      "UNKNOWN",
    ] as const),
    clock_id: readOptionalString(record, "clock_id", `${path}.clock_id`),
    offset_to_utc_ns: readOptionalString(
      record,
      "offset_to_utc_ns",
      `${path}.offset_to_utc_ns`,
    ),
    drift_ppm: readOptionalNumber(record, "drift_ppm", `${path}.drift_ppm`),
    uncertainty_ns: readOptionalString(
      record,
      "uncertainty_ns",
      `${path}.uncertainty_ns`,
    ),
    sync_method: readEnum(record, "sync_method", `${path}.sync_method`, [
      "PTP",
      "NTP",
      "HARDWARE_TRIGGER",
      "SOFTWARE_ESTIMATE",
      "MANUAL",
      "NONE",
    ] as const),
    sync_ref: readOptionalString(record, "sync_ref", `${path}.sync_ref`),
    sync_status: readEnum(record, "sync_status", `${path}.sync_status`, [
      "VERIFIED",
      "DEGRADED",
      "UNVERIFIED",
      "FAILED",
    ] as const),
  };
}

function parseCalibrationSnapshotItem(
  value: unknown,
  path: string,
): CalibrationSnapshotItem {
  const record = asRecord(value, path);
  return {
    calibration_id: readUuid(
      record,
      "calibration_id",
      `${path}.calibration_id`,
    ),
    calibration_version: readPositiveInteger(
      record,
      "calibration_version",
      `${path}.calibration_version`,
    ),
    checksum: readSha256Checksum(record, "checksum", `${path}.checksum`),
    target_type: readEnum(record, "target_type", `${path}.target_type`, [
      "ROBOT",
      "DEVICE",
      "SENSOR",
      "SYSTEM",
    ] as const),
    target_id: readUuid(record, "target_id", `${path}.target_id`),
    schema_version: readString(
      record,
      "schema_version",
      `${path}.schema_version`,
    ),
  };
}

function parsePreviewRef(value: unknown, path: string): EpisodePreviewRef {
  const record = asRecord(value, path);
  const rawSha = record.sha256;
  return {
    preview_id: readString(record, "preview_id", `${path}.preview_id`),
    object_key: readString(record, "object_key", `${path}.object_key`),
    content_type: readString(record, "content_type", `${path}.content_type`),
    sha256:
      rawSha === undefined || rawSha === null
        ? null
        : readSha256Checksum(record, "sha256", `${path}.sha256`),
  };
}

function readArray<T>(
  record: Record<string, unknown>,
  key: string,
  path: string,
  parseItem: (value: unknown, itemPath: string) => T,
): T[] {
  const value = record[key] ?? [];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value.map((item, index) => parseItem(item, `${path}[${index}]`));
}

export function parseEpisode(value: unknown, path = "Episode"): Episode {
  const record = asRecord(value, path);
  const timeBase = parseTimeBase(record.time_base, `${path}.time_base`);
  const calibrationSnapshot = readArray(
    record,
    "calibration_snapshot",
    `${path}.calibration_snapshot`,
    parseCalibrationSnapshotItem,
  );
  if (calibrationSnapshot.length === 0) {
    throw new ContractValidationError(`${path}.calibration_snapshot 不能为空`);
  }
  const rawAssetIds = readUuidArray(record, "raw_asset_ids", `${path}.raw_asset_ids`);
  if (rawAssetIds.length === 0) {
    throw new ContractValidationError(`${path}.raw_asset_ids 不能为空`);
  }

  return {
    episode_id: readUuid(record, "episode_id", `${path}.episode_id`),
    episode_lineage_id: readUuid(
      record,
      "episode_lineage_id",
      `${path}.episode_lineage_id`,
    ),
    episode_version: readPositiveInteger(
      record,
      "episode_version",
      `${path}.episode_version`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    task_id: readOptionalUuid(record, "task_id", `${path}.task_id`),
    batch_id: readOptionalUuid(record, "batch_id", `${path}.batch_id`),
    robot_id: readOptionalUuid(record, "robot_id", `${path}.robot_id`),
    device_ids: readUuidArrayOrEmpty(
      record,
      "device_ids",
      `${path}.device_ids`,
    ),
    sensor_ids: readUuidArrayOrEmpty(
      record,
      "sensor_ids",
      `${path}.sensor_ids`,
    ),
    raw_asset_ids: rawAssetIds,
    start_time_ns: readString(record, "start_time_ns", `${path}.start_time_ns`),
    end_time_ns: readString(record, "end_time_ns", `${path}.end_time_ns`),
    duration_ns: readString(record, "duration_ns", `${path}.duration_ns`),
    time_domain: readEnum(record, "time_domain", `${path}.time_domain`, [
      "UTC",
    ] as const),
    time_base: timeBase,
    canonical_schema_version: readString(
      record,
      "canonical_schema_version",
      `${path}.canonical_schema_version`,
    ),
    pipeline_id: readString(record, "pipeline_id", `${path}.pipeline_id`),
    pipeline_version: readString(
      record,
      "pipeline_version",
      `${path}.pipeline_version`,
    ),
    segmentation_rule_version: readString(
      record,
      "segmentation_rule_version",
      `${path}.segmentation_rule_version`,
    ),
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      EPISODE_STATUS_VALUES,
    ),
    quality_status: readEnum(
      record,
      "quality_status",
      `${path}.quality_status`,
      EPISODE_QUALITY_STATUS_VALUES,
    ),
    annotation_status: readEnum(
      record,
      "annotation_status",
      `${path}.annotation_status`,
      EPISODE_ANNOTATION_STATUS_VALUES,
    ),
    success: readOptionalBoolean(record, "success", `${path}.success`),
    failure_reason: readOptionalString(
      record,
      "failure_reason",
      `${path}.failure_reason`,
    ),
    scene_labels:
      readOptionalStringArray(
        record,
        "scene_labels",
        `${path}.scene_labels`,
      ) ?? [],
    language_instruction: readOptionalString(
      record,
      "language_instruction",
      `${path}.language_instruction`,
    ),
    frame_count: readInteger(record, "frame_count", `${path}.frame_count`),
    calibration_snapshot: calibrationSnapshot,
    manifest_key: readString(record, "manifest_key", `${path}.manifest_key`),
    manifest_sha256: readSha256Checksum(
      record,
      "manifest_sha256",
      `${path}.manifest_sha256`,
    ),
    preview_refs: readArray(
      record,
      "preview_refs",
      `${path}.preview_refs`,
      parsePreviewRef,
    ),
    supersedes_id: readOptionalUuid(
      record,
      "supersedes_id",
      `${path}.supersedes_id`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readString(record, "created_at", `${path}.created_at`),
    updated_at: readString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
  };
}

export function parseEpisodePage(value: unknown): EpisodePage {
  return parseCursorPage(value, "Episode 列表", parseEpisode);
}
