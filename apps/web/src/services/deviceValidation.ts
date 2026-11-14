import type {
  CalibrationRecord,
  CursorPage,
  Device,
  Sensor,
} from "../types/device";
import {
  CALIBRATION_SCOPE_VALUES,
  CALIBRATION_TARGET_TYPE_VALUES,
  CALIBRATION_TYPE_VALUES,
  DEVICE_TYPE_VALUES,
  FRAME_CONVENTION_VALUES,
  SENSOR_SYNC_MODE_VALUES,
  SENSOR_TYPE_VALUES,
} from "../types/device";
import {
  ContractValidationError,
  asRecord,
  readEnum,
  readInteger,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readOptionalUuid,
  readSha256Checksum,
  readString,
  readUuid,
  readUuidArray,
} from "./validation";

function parseCursorPage<T>(
  value: unknown,
  path: string,
  parseItem: (item: unknown, itemPath: string) => T,
): CursorPage<T> {
  const record = asRecord(value, path);
  const rawItems = record.items;
  if (!Array.isArray(rawItems)) {
    throw new ContractValidationError(`${path}.items 必须是数组`);
  }
  const items = rawItems.map((item, index) =>
    parseItem(item, `${path}.items[${index}]`),
  );
  const rawNextCursor = record.next_cursor;
  if (
    rawNextCursor !== undefined &&
    rawNextCursor !== null &&
    typeof rawNextCursor !== "string"
  ) {
    throw new ContractValidationError(
      `${path}.next_cursor 必须是字符串或 null`,
    );
  }
  return { items, next_cursor: rawNextCursor ?? null };
}

export function parseDevice(value: unknown, path = "设备"): Device {
  const record = asRecord(value, path);
  return {
    device_id: readUuid(record, "device_id", `${path}.device_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    robot_id: readOptionalUuid(record, "robot_id", `${path}.robot_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    device_type: readEnum(
      record,
      "device_type",
      `${path}.device_type`,
      DEVICE_TYPE_VALUES,
    ),
    vendor: readOptionalString(record, "vendor", `${path}.vendor`),
    model: readOptionalString(record, "model", `${path}.model`),
    serial_number: readOptionalString(
      record,
      "serial_number",
      `${path}.serial_number`,
    ),
    firmware_version: readOptionalString(
      record,
      "firmware_version",
      `${path}.firmware_version`,
    ),
    hardware_version: readOptionalString(
      record,
      "hardware_version",
      `${path}.hardware_version`,
    ),
    calibration_profile_id: readOptionalUuid(
      record,
      "calibration_profile_id",
      `${path}.calibration_profile_id`,
    ),
    capability_profile: readOptionalRecord(
      record,
      "capability_profile",
      `${path}.capability_profile`,
    ),
    status: readString(record, "status", `${path}.status`),
    location_id: readOptionalUuid(record, "location_id", `${path}.location_id`),
    owner_user_id: readOptionalUuid(
      record,
      "owner_user_id",
      `${path}.owner_user_id`,
    ),
    version: readInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
    freshness: readOptionalString(record, "freshness", `${path}.freshness`),
    last_synced_at: readOptionalString(
      record,
      "last_synced_at",
      `${path}.last_synced_at`,
    ),
    last_heartbeat_at: readOptionalString(
      record,
      "last_heartbeat_at",
      `${path}.last_heartbeat_at`,
    ),
    heartbeat_timeout_seconds: readOptionalNumber(
      record,
      "heartbeat_timeout_seconds",
      `${path}.heartbeat_timeout_seconds`,
    ),
  };
}

export function parseDevicePage(value: unknown): CursorPage<Device> {
  return parseCursorPage(value, "设备列表", parseDevice);
}

export function parseSensor(value: unknown, path = "传感器"): Sensor {
  const record = asRecord(value, path);
  return {
    sensor_id: readUuid(record, "sensor_id", `${path}.sensor_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    robot_id: readOptionalUuid(record, "robot_id", `${path}.robot_id`),
    device_id: readOptionalUuid(record, "device_id", `${path}.device_id`),
    code: readString(record, "code", `${path}.code`),
    sensor_type: readEnum(
      record,
      "sensor_type",
      `${path}.sensor_type`,
      SENSOR_TYPE_VALUES,
    ),
    vendor: readOptionalString(record, "vendor", `${path}.vendor`),
    model: readOptionalString(record, "model", `${path}.model`),
    serial_number: readOptionalString(
      record,
      "serial_number",
      `${path}.serial_number`,
    ),
    sample_rate_hz: readOptionalNumber(
      record,
      "sample_rate_hz",
      `${path}.sample_rate_hz`,
    ),
    resolution_w: readOptionalNumber(
      record,
      "resolution_w",
      `${path}.resolution_w`,
    ),
    resolution_h: readOptionalNumber(
      record,
      "resolution_h",
      `${path}.resolution_h`,
    ),
    bit_depth: readOptionalNumber(record, "bit_depth", `${path}.bit_depth`),
    channels: readOptionalNumber(record, "channels", `${path}.channels`),
    intrinsic_calibration_id: readOptionalUuid(
      record,
      "intrinsic_calibration_id",
      `${path}.intrinsic_calibration_id`,
    ),
    extrinsic_calibration_id: readOptionalUuid(
      record,
      "extrinsic_calibration_id",
      `${path}.extrinsic_calibration_id`,
    ),
    frame_id: readOptionalString(record, "frame_id", `${path}.frame_id`),
    frame_convention: readEnum(
      record,
      "frame_convention",
      `${path}.frame_convention`,
      FRAME_CONVENTION_VALUES,
    ),
    // ADR-0019 阶段 1 仍读取旧字段；阶段 2 删除。
    coordinate_frame: readOptionalString(
      record,
      "coordinate_frame",
      `${path}.coordinate_frame`,
    ),
    sync_mode: readEnum(
      record,
      "sync_mode",
      `${path}.sync_mode`,
      SENSOR_SYNC_MODE_VALUES,
    ),
    sync_group: readOptionalString(record, "sync_group", `${path}.sync_group`),
    firmware_version: readOptionalString(
      record,
      "firmware_version",
      `${path}.firmware_version`,
    ),
    calibration_profile_id: readOptionalUuid(
      record,
      "calibration_profile_id",
      `${path}.calibration_profile_id`,
    ),
    status: readString(record, "status", `${path}.status`),
    mount_position: readOptionalString(
      record,
      "mount_position",
      `${path}.mount_position`,
    ),
    version: readInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
  };
}

export function parseSensorPage(value: unknown): CursorPage<Sensor> {
  return parseCursorPage(value, "传感器列表", parseSensor);
}

export function parseCalibrationRecord(
  value: unknown,
  path = "标定记录",
): CalibrationRecord {
  const record = asRecord(value, path);
  const uncertainty = readOptionalRecord(
    record,
    "uncertainty",
    `${path}.uncertainty`,
  );
  const evidenceAssetIds = readUuidArray(
    record,
    "evidence_asset_ids",
    `${path}.evidence_asset_ids`,
  );

  return {
    calibration_id: readUuid(
      record,
      "calibration_id",
      `${path}.calibration_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    profile_id: readUuid(record, "profile_id", `${path}.profile_id`),
    scope: readEnum(
      record,
      "scope",
      `${path}.scope`,
      CALIBRATION_SCOPE_VALUES,
    ),
    target_type: readEnum(
      record,
      "target_type",
      `${path}.target_type`,
      CALIBRATION_TARGET_TYPE_VALUES,
    ),
    target_id: readUuid(record, "target_id", `${path}.target_id`),
    calibration_type: readEnum(
      record,
      "calibration_type",
      `${path}.calibration_type`,
      CALIBRATION_TYPE_VALUES,
    ),
    method: readString(record, "method", `${path}.method`),
    parameters: readOptionalRecord(
      record,
      "parameters",
      `${path}.parameters`,
    ),
    parent_frame: readOptionalString(
      record,
      "parent_frame",
      `${path}.parent_frame`,
    ),
    child_frame: readOptionalString(
      record,
      "child_frame",
      `${path}.child_frame`,
    ),
    valid_from: readOptionalString(record, "valid_from", `${path}.valid_from`),
    valid_to: readOptionalString(record, "valid_to", `${path}.valid_to`),
    status: readString(record, "status", `${path}.status`),
    evidence_asset_ids: evidenceAssetIds,
    source: readOptionalString(record, "source", `${path}.source`),
    checksum: readSha256Checksum(record, "checksum", `${path}.checksum`),
    uncertainty,
    performed_by: readUuid(
      record,
      "performed_by",
      `${path}.performed_by`,
    ),
    validated_by: readOptionalUuid(
      record,
      "validated_by",
      `${path}.validated_by`,
    ),
    validated_at: readOptionalString(
      record,
      "validated_at",
      `${path}.validated_at`,
    ),
    supersedes_id: readOptionalUuid(
      record,
      "supersedes_id",
      `${path}.supersedes_id`,
    ),
    version: readInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
  };
}

export function parseCalibrationPage(
  value: unknown,
): CursorPage<CalibrationRecord> {
  return parseCursorPage(value, "标定记录列表", parseCalibrationRecord);
}
