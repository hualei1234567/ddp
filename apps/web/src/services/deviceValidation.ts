import type {
  CalibrationRecord,
  CursorPage,
  Device,
  Sensor,
} from "../types/device";
import {
  ContractValidationError,
  asRecord,
  readInteger,
  readOptionalNumber,
  readOptionalRecord,
  readOptionalString,
  readOptionalStringArray,
  readString,
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
    device_id: readString(record, "device_id", `${path}.device_id`),
    project_id: readString(record, "project_id", `${path}.project_id`),
    robot_id: readOptionalString(record, "robot_id", `${path}.robot_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    device_type: readString(record, "device_type", `${path}.device_type`),
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
    calibration_profile_id: readOptionalString(
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
    location_id: readOptionalString(
      record,
      "location_id",
      `${path}.location_id`,
    ),
    owner_user_id: readOptionalString(
      record,
      "owner_user_id",
      `${path}.owner_user_id`,
    ),
    version: readInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(record, "created_at", `${path}.created_at`),
    updated_at: readOptionalString(record, "updated_at", `${path}.updated_at`),
    created_by: readOptionalString(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalString(record, "updated_by", `${path}.updated_by`),
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
    sensor_id: readString(record, "sensor_id", `${path}.sensor_id`),
    project_id: readString(record, "project_id", `${path}.project_id`),
    robot_id: readOptionalString(record, "robot_id", `${path}.robot_id`),
    device_id: readOptionalString(record, "device_id", `${path}.device_id`),
    code: readString(record, "code", `${path}.code`),
    sensor_type: readString(record, "sensor_type", `${path}.sensor_type`),
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
    intrinsic_calibration_id: readOptionalString(
      record,
      "intrinsic_calibration_id",
      `${path}.intrinsic_calibration_id`,
    ),
    extrinsic_calibration_id: readOptionalString(
      record,
      "extrinsic_calibration_id",
      `${path}.extrinsic_calibration_id`,
    ),
    coordinate_frame: readOptionalString(
      record,
      "coordinate_frame",
      `${path}.coordinate_frame`,
    ),
    sync_mode: readOptionalString(record, "sync_mode", `${path}.sync_mode`),
    sync_group: readOptionalString(record, "sync_group", `${path}.sync_group`),
    firmware_version: readOptionalString(
      record,
      "firmware_version",
      `${path}.firmware_version`,
    ),
    calibration_profile_id: readOptionalString(
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
  const evidenceAssetIds = readOptionalStringArray(
    record,
    "evidence_asset_ids",
    `${path}.evidence_asset_ids`,
  );

  return {
    calibration_id: readString(
      record,
      "calibration_id",
      `${path}.calibration_id`,
    ),
    project_id: readString(record, "project_id", `${path}.project_id`),
    profile_id: readOptionalString(record, "profile_id", `${path}.profile_id`),
    scope: readOptionalString(record, "scope", `${path}.scope`),
    target_type: readString(record, "target_type", `${path}.target_type`),
    target_id: readString(record, "target_id", `${path}.target_id`),
    calibration_type: readString(
      record,
      "calibration_type",
      `${path}.calibration_type`,
    ),
    method: readOptionalString(record, "method", `${path}.method`),
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
    evidence_asset_ids: evidenceAssetIds ?? [],
    source: readOptionalString(record, "source", `${path}.source`),
    checksum: readOptionalString(record, "checksum", `${path}.checksum`),
    uncertainty,
    performed_by: readOptionalString(
      record,
      "performed_by",
      `${path}.performed_by`,
    ),
    validated_by: readOptionalString(
      record,
      "validated_by",
      `${path}.validated_by`,
    ),
    validated_at: readOptionalString(
      record,
      "validated_at",
      `${path}.validated_at`,
    ),
    supersedes_id: readOptionalString(
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
