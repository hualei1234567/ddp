import type {
  CalibrationProfile,
  CalibrationRecord,
  CursorPage,
  Device,
  Robot,
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
  readPositiveInteger,
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
    // G-09：状态语义六列。缺失或 null 都按 null 读取——后端允许历史记录为空，
    // 但类型一旦出现就必须是字符串/UUID，绝不把非法类型静默当成 undefined。
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    fault_code: readOptionalString(record, "fault_code", `${path}.fault_code`),
    fault_detail: readOptionalString(
      record,
      "fault_detail",
      `${path}.fault_detail`,
    ),
    fault_occurred_at: readOptionalString(
      record,
      "fault_occurred_at",
      `${path}.fault_occurred_at`,
    ),
    status_changed_at: readOptionalString(
      record,
      "status_changed_at",
      `${path}.status_changed_at`,
    ),
    status_changed_by: readOptionalUuid(
      record,
      "status_changed_by",
      `${path}.status_changed_by`,
    ),
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
    // G-09：与 Device 一致的状态语义六列。
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    fault_code: readOptionalString(record, "fault_code", `${path}.fault_code`),
    fault_detail: readOptionalString(
      record,
      "fault_detail",
      `${path}.fault_detail`,
    ),
    fault_occurred_at: readOptionalString(
      record,
      "fault_occurred_at",
      `${path}.fault_occurred_at`,
    ),
    status_changed_at: readOptionalString(
      record,
      "status_changed_at",
      `${path}.status_changed_at`,
    ),
    status_changed_by: readOptionalUuid(
      record,
      "status_changed_by",
      `${path}.status_changed_by`,
    ),
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
    calibration_version: readInteger(
      record,
      "calibration_version",
      `${path}.calibration_version`,
    ),
    profile_version: readOptionalVersion(record, `${path}.profile_version`),
    profile_schema_version: readOptionalString(
      record,
      "profile_schema_version",
      `${path}.profile_schema_version`,
    ),
    profile_snapshot:
      readOptionalRecord(record, "profile_snapshot", `${path}.profile_snapshot`) ??
      {},
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

/**
 * profile_version 是 `integer | null`：null 表示历史记录未固化版本（ADR-0020）。
 * 这里对 null 与缺失都返回 null，对非整数抛错。
 */
function readOptionalVersion(
  record: Record<string, unknown>,
  path: string,
): number | null {
  const value = record.profile_version;
  if (value === undefined || value === null) {
    return null;
  }
  return readInteger(record, "profile_version", path);
}

/**
 * Robot 响应的严格解析。
 *
 * `capability_profile` 允许扩展，但必须是对象——服务端保证这一点，
 * 前端不在类型层面猜测里面的键。
 */
export function parseRobot(value: unknown, path = "机器人"): Robot {
  const record = asRecord(value, path);
  return {
    robot_id: readUuid(record, "robot_id", `${path}.robot_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    name: readString(record, "name", `${path}.name`),
    robot_type: readString(record, "robot_type", `${path}.robot_type`),
    status: readString(record, "status", `${path}.status`),
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
    capability_profile:
      readOptionalRecord(
        record,
        "capability_profile",
        `${path}.capability_profile`,
      ) ?? {},
    location_id: readOptionalUuid(record, "location_id", `${path}.location_id`),
    owner_user_id: readOptionalUuid(
      record,
      "owner_user_id",
      `${path}.owner_user_id`,
    ),
    status_reason: readOptionalString(
      record,
      "status_reason",
      `${path}.status_reason`,
    ),
    fault_code: readOptionalString(record, "fault_code", `${path}.fault_code`),
    fault_detail: readOptionalString(
      record,
      "fault_detail",
      `${path}.fault_detail`,
    ),
    fault_occurred_at: readOptionalString(
      record,
      "fault_occurred_at",
      `${path}.fault_occurred_at`,
    ),
    status_changed_at: readOptionalString(
      record,
      "status_changed_at",
      `${path}.status_changed_at`,
    ),
    status_changed_by: readOptionalUuid(
      record,
      "status_changed_by",
      `${path}.status_changed_by`,
    ),
    version: readPositiveInteger(record, "version", `${path}.version`),
  };
}

export function parseRobotPage(value: unknown): CursorPage<Robot> {
  return parseCursorPage(value, "机器人列表", parseRobot);
}

export function parseCalibrationProfile(
  value: unknown,
  path = "标定配置",
): CalibrationProfile {
  const record = asRecord(value, path);
  const requiredTypes = record.required_types;
  if (!Array.isArray(requiredTypes)) {
    throw new ContractValidationError(`${path}.required_types 必须是数组`);
  }
  return {
    profile_id: readUuid(record, "profile_id", `${path}.profile_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    name: readString(record, "name", `${path}.name`),
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
    profile_version: readPositiveInteger(
      record,
      "profile_version",
      `${path}.profile_version`,
    ),
    schema_version: readString(
      record,
      "schema_version",
      `${path}.schema_version`,
    ),
    required_types: requiredTypes.map((item, index) => {
      const value = String(item);
      const matched = CALIBRATION_TYPE_VALUES.find(
        (candidate) => candidate === value,
      );
      if (matched === undefined) {
        throw new ContractValidationError(
          `${path}.required_types[${index}] 必须是合法 CalibrationType`,
        );
      }
      return matched;
    }),
    selection_rule:
      readOptionalRecord(record, "selection_rule", `${path}.selection_rule`) ??
      {},
    target_id: readOptionalUuid(record, "target_id", `${path}.target_id`),
  };
}
