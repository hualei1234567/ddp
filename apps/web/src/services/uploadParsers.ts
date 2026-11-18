import type {
  CompleteUploadResponse,
  DownloadUrl,
  RawAsset,
  RawAssetPage,
  UploadPartAckResponse,
  UploadPartList,
  UploadPartSummary,
  UploadPartUrlResponse,
  UploadSession,
} from "../types/collection";
import {
  ASSET_FORMAT_VALUES,
  RAW_ASSET_STATUS_VALUES,
  UPLOAD_SESSION_STATUS_VALUES,
  UPLOAD_VERIFICATION_STATUS_VALUES,
} from "../types/collection";
import {
  asRecord,
  readBoolean,
  readInteger,
  readOptionalIntegerArray,
  readOptionalRecord,
  readOptionalString,
  readOptionalStringArray,
  readOptionalUuid,
  readPositiveInteger,
  readSha256Checksum,
  readString,
  readStringArray,
  readUuid,
  readUuidArray,
  parseCursorPage,
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

function readStringArrayOrEmpty(
  record: Record<string, unknown>,
  key: string,
  path: string,
): string[] {
  return readOptionalStringArray(record, key, path) ?? [];
}

export function parseUploadPartSummary(
  value: unknown,
  path = "上传分片",
): UploadPartSummary {
  const record = asRecord(value, path);
  const partNumber = readPositiveInteger(
    record,
    "part_number",
    `${path}.part_number`,
  );
  if (partNumber > 10_000) {
    throw new ContractValidationError(`${path}.part_number 不得超过 10000`);
  }
  return {
    part_number: partNumber,
    size_bytes: readPositiveInteger(record, "size_bytes", `${path}.size_bytes`),
    etag: readString(record, "etag", `${path}.etag`),
    sha256: readSha256Checksum(record, "sha256", `${path}.sha256`),
    recorded_at: readString(record, "recorded_at", `${path}.recorded_at`),
  };
}

export function parseUploadSession(
  value: unknown,
  path = "上传会话",
): UploadSession {
  const record = asRecord(value, path);
  const rawParts = record.uploaded_parts ?? [];
  if (!Array.isArray(rawParts)) {
    throw new ContractValidationError(`${path}.uploaded_parts 必须是数组`);
  }
  const uploadedParts = rawParts.map((item, index) =>
    parseUploadPartSummary(item, `${path}.uploaded_parts[${index}]`),
  );
  const objectKeyPrefix = readOptionalString(
    record,
    "object_key_prefix",
    `${path}.object_key_prefix`,
  );
  const formatHintValue = record.format_hint;

  return {
    upload_session_id: readUuid(
      record,
      "upload_session_id",
      `${path}.upload_session_id`,
    ),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    collection_task_id: readOptionalUuid(
      record,
      "collection_task_id",
      `${path}.collection_task_id`,
    ),
    batch_id: readOptionalUuid(record, "batch_id", `${path}.batch_id`),
    status: readEnumValue(
      record,
      "status",
      `${path}.status`,
      UPLOAD_SESSION_STATUS_VALUES,
    ),
    verification_status: readEnumValue(
      record,
      "verification_status",
      `${path}.verification_status`,
      UPLOAD_VERIFICATION_STATUS_VALUES,
    ),
    verification_error_code: readOptionalString(
      record,
      "verification_error_code",
      `${path}.verification_error_code`,
    ),
    verified_at: readOptionalString(record, "verified_at", `${path}.verified_at`),
    original_filename: readString(
      record,
      "original_filename",
      `${path}.original_filename`,
    ),
    declared_content_type: readString(
      record,
      "declared_content_type",
      `${path}.declared_content_type`,
    ),
    declared_size_bytes: readPositiveInteger(
      record,
      "declared_size_bytes",
      `${path}.declared_size_bytes`,
    ),
    expected_sha256:
      record.expected_sha256 === undefined || record.expected_sha256 === null
        ? null
        : readSha256Checksum(
            record,
            "expected_sha256",
            `${path}.expected_sha256`,
          ),
    format_hint:
      formatHintValue === undefined || formatHintValue === null
        ? null
        : readEnumValue(
            record,
            "format_hint",
            `${path}.format_hint`,
            ASSET_FORMAT_VALUES,
          ),
    part_size_bytes: readPositiveInteger(
      record,
      "part_size_bytes",
      `${path}.part_size_bytes`,
    ),
    max_size_bytes: readPositiveInteger(
      record,
      "max_size_bytes",
      `${path}.max_size_bytes`,
    ),
    allowed_content_types: readStringArrayOrEmpty(
      record,
      "allowed_content_types",
      `${path}.allowed_content_types`,
    ),
    object_key_prefix: objectKeyPrefix ?? undefined,
    object_key: readOptionalString(record, "object_key", `${path}.object_key`),
    expires_at: readString(record, "expires_at", `${path}.expires_at`),
    completed_at: readOptionalString(
      record,
      "completed_at",
      `${path}.completed_at`,
    ),
    aborted_at: readOptionalString(record, "aborted_at", `${path}.aborted_at`),
    failure_code: readOptionalString(
      record,
      "failure_code",
      `${path}.failure_code`,
    ),
    failure_summary: readOptionalString(
      record,
      "failure_summary",
      `${path}.failure_summary`,
    ),
    uploaded_bytes: readInteger(
      record,
      "uploaded_bytes",
      `${path}.uploaded_bytes`,
    ),
    uploaded_part_count: readInteger(
      record,
      "uploaded_part_count",
      `${path}.uploaded_part_count`,
    ),
    missing_part_numbers: readOptionalIntegerArray(
      record,
      "missing_part_numbers",
      `${path}.missing_part_numbers`,
    ),
    uploaded_parts: uploadedParts,
    deduplicated: readBoolean(record, "deduplicated", `${path}.deduplicated`),
    deduplicated_asset_id: readOptionalUuid(
      record,
      "deduplicated_asset_id",
      `${path}.deduplicated_asset_id`,
    ),
    asset_id: readOptionalUuid(record, "asset_id", `${path}.asset_id`),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readString(record, "created_at", `${path}.created_at`),
    updated_at: readString(record, "updated_at", `${path}.updated_at`),
    // 契约未将 created_by 列为 required，服务端类型也允许 null。
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
  };
}

// 局部泛型别名，避免与 validation 的 readEnum 签名在复杂联合类型上产生噪声。
function readEnumValue<T extends string>(
  record: Record<string, unknown>,
  key: string,
  path: string,
  allowed: readonly T[],
): T {
  const value = readString(record, key, path);
  const matched = allowed.find((item) => item === value);
  if (matched === undefined) {
    throw new ContractValidationError(
      `${path} 必须是以下枚举之一：${allowed.join(" / ")}`,
    );
  }
  return matched;
}

export function parseUploadPartUrl(
  value: unknown,
  path = "分片上传地址",
): UploadPartUrlResponse {
  const record = asRecord(value, path);
  const method = readString(record, "method", `${path}.method`);
  if (method !== "PUT") {
    throw new ContractValidationError(`${path}.method 必须为 PUT`);
  }
  const rawHeaders = asRecord(record.headers, `${path}.headers`);
  const headers: Record<string, string> = {};
  for (const [key, headerValue] of Object.entries(rawHeaders)) {
    if (typeof headerValue !== "string") {
      throw new ContractValidationError(
        `${path}.headers.${key} 必须是字符串`,
      );
    }
    headers[key] = headerValue;
  }
  return {
    part_number: readPositiveInteger(
      record,
      "part_number",
      `${path}.part_number`,
    ),
    method: "PUT",
    url: readString(record, "url", `${path}.url`),
    headers,
    expires_at: readString(record, "expires_at", `${path}.expires_at`),
  };
}

export function parseUploadPartAck(
  value: unknown,
  path = "分片确认结果",
): UploadPartAckResponse {
  const record = asRecord(value, path);
  return {
    part_number: readPositiveInteger(
      record,
      "part_number",
      `${path}.part_number`,
    ),
    etag: readString(record, "etag", `${path}.etag`),
    size_bytes: readPositiveInteger(record, "size_bytes", `${path}.size_bytes`),
    sha256: readSha256Checksum(record, "sha256", `${path}.sha256`),
    recorded_at: readString(record, "recorded_at", `${path}.recorded_at`),
    uploaded_bytes: readInteger(
      record,
      "uploaded_bytes",
      `${path}.uploaded_bytes`,
    ),
    uploaded_part_count: readInteger(
      record,
      "uploaded_part_count",
      `${path}.uploaded_part_count`,
    ),
  };
}

export function parseUploadPartList(
  value: unknown,
  path = "已确认分片列表",
): UploadPartList {
  const record = asRecord(value, path);
  const rawItems = record.items;
  if (!Array.isArray(rawItems)) {
    throw new ContractValidationError(`${path}.items 必须是数组`);
  }
  return {
    items: rawItems.map((item, index) =>
      parseUploadPartSummary(item, `${path}.items[${index}]`),
    ),
    missing_part_numbers: readOptionalIntegerArray(
      record,
      "missing_part_numbers",
      `${path}.missing_part_numbers`,
    ),
    uploaded_bytes: readInteger(
      record,
      "uploaded_bytes",
      `${path}.uploaded_bytes`,
    ),
  };
}

export function parseRawAsset(value: unknown, path = "原始资产"): RawAsset {
  const record = asRecord(value, path);
  return {
    asset_id: readUuid(record, "asset_id", `${path}.asset_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    code: readString(record, "code", `${path}.code`),
    source_type: readEnumValue(record, "source_type", `${path}.source_type`, [
      "UPLOAD",
      "DEVICE",
      "IMPORT",
    ] as const),
    collection_task_id: readOptionalUuid(
      record,
      "collection_task_id",
      `${path}.collection_task_id`,
    ),
    batch_id: readOptionalUuid(record, "batch_id", `${path}.batch_id`),
    upload_session_id: readOptionalUuid(
      record,
      "upload_session_id",
      `${path}.upload_session_id`,
    ),
    original_filename: readString(
      record,
      "original_filename",
      `${path}.original_filename`,
    ),
    object_key: readString(record, "object_key", `${path}.object_key`),
    size_bytes: readPositiveInteger(record, "size_bytes", `${path}.size_bytes`),
    content_type: readString(record, "content_type", `${path}.content_type`),
    format: readEnumValue(record, "format", `${path}.format`, ASSET_FORMAT_VALUES),
    sha256: readSha256Checksum(record, "sha256", `${path}.sha256`),
    status: readEnumValue(
      record,
      "status",
      `${path}.status`,
      RAW_ASSET_STATUS_VALUES,
    ),
    captured_at: readOptionalString(
      record,
      "captured_at",
      `${path}.captured_at`,
    ),
    ingested_at: readOptionalString(
      record,
      "ingested_at",
      `${path}.ingested_at`,
    ),
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
    sensor_snapshot: readOptionalRecord(
      record,
      "sensor_snapshot",
      `${path}.sensor_snapshot`,
    ),
    device_time_raw: readOptionalRecord(
      record,
      "device_time_raw",
      `${path}.device_time_raw`,
    ),
    raw_metadata:
      readOptionalRecord(record, "raw_metadata", `${path}.raw_metadata`) ?? {},
    storage_class: readEnumValue(
      record,
      "storage_class",
      `${path}.storage_class`,
      ["STANDARD", "INFREQUENT", "ARCHIVE"] as const,
    ),
    retention_policy_id: readOptionalUuid(
      record,
      "retention_policy_id",
      `${path}.retention_policy_id`,
    ),
    quarantine_reason: readOptionalString(
      record,
      "quarantine_reason",
      `${path}.quarantine_reason`,
    ),
    quarantine_codes: readStringArrayOrEmpty(
      record,
      "quarantine_codes",
      `${path}.quarantine_codes`,
    ),
    scan_report_key: readOptionalString(
      record,
      "scan_report_key",
      `${path}.scan_report_key`,
    ),
    verified_at: readOptionalString(
      record,
      "verified_at",
      `${path}.verified_at`,
    ),
    archived_at: readOptionalString(
      record,
      "archived_at",
      `${path}.archived_at`,
    ),
    tombstoned_at: readOptionalString(
      record,
      "tombstoned_at",
      `${path}.tombstoned_at`,
    ),
    duplicate_of_asset_id: readOptionalUuid(
      record,
      "duplicate_of_asset_id",
      `${path}.duplicate_of_asset_id`,
    ),
    uploaded_by: readUuid(record, "uploaded_by", `${path}.uploaded_by`),
    version: readPositiveInteger(record, "version", `${path}.version`),
    created_at: readOptionalString(
      record,
      "created_at",
      `${path}.created_at`,
    ),
    updated_at: readOptionalString(
      record,
      "updated_at",
      `${path}.updated_at`,
    ),
    created_by: readOptionalUuid(record, "created_by", `${path}.created_by`),
    updated_by: readOptionalUuid(record, "updated_by", `${path}.updated_by`),
    // 契约要求服务端下发；缺失即视为响应不可信，而不是静默当成"无动作"。
    allowed_actions: readStringArray(record, "allowed_actions", `${path}.allowed_actions`),
  };
}

export function parseRawAssetPage(value: unknown): RawAssetPage {
  return parseCursorPage(value, "原始资产列表", parseRawAsset);
}

export function parseCompleteUploadResponse(
  value: unknown,
  path = "完成上传响应",
): CompleteUploadResponse {
  const record = asRecord(value, path);
  const rawAssetValue = record.raw_asset;
  return {
    upload_session: parseUploadSession(
      record.upload_session,
      `${path}.upload_session`,
    ),
    raw_asset:
      rawAssetValue === undefined || rawAssetValue === null
        ? null
        : parseRawAsset(rawAssetValue, `${path}.raw_asset`),
    deduplicated: readBoolean(
      record,
      "deduplicated",
      `${path}.deduplicated`,
    ),
    validation_job_id: readUuid(
      record,
      "validation_job_id",
      `${path}.validation_job_id`,
    ),
    replayed: readBoolean(record, "replayed", `${path}.replayed`),
  };
}

/**
 * 下载地址来自服务端签发。只允许 http/https，避免把 javascript:/data:
 * 等协议交给浏览器打开；expires_at 必须是可解析时间。
 */
export function parseDownloadUrl(
  value: unknown,
  path = "下载地址",
): DownloadUrl {
  const record = asRecord(value, path);
  const url = readString(record, "url", `${path}.url`);
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new ContractValidationError(`${path}.url 不是合法 URL`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new ContractValidationError(`${path}.url 只允许 http/https`);
  }
  const expiresAt = readString(record, "expires_at", `${path}.expires_at`);
  if (Number.isNaN(Date.parse(expiresAt))) {
    throw new ContractValidationError(`${path}.expires_at 不是合法时间`);
  }
  return { url, expires_at: expiresAt };
}
