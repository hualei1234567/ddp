/** 数据源与导入任务的严格响应解析器。 */

import type {
  AccessCondition,
  DataSource,
  DataSourceImport,
  DataSourceKind,
  ImportScope,
  ImportStatus,
} from "../types/datasource";
import {
  asRecord,
  readBoolean,
  readEnum,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

const KINDS: readonly DataSourceKind[] = ["HUGGINGFACE"];
const ACCESS: readonly AccessCondition[] = [
  "PUBLIC",
  "GATED",
  "PRIVATE",
  "UNKNOWN",
];
const SCOPES: readonly ImportScope[] = ["SAMPLE", "SPLIT", "FULL"];
const STATUSES: readonly ImportStatus[] = [
  "PENDING",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
];

/** 可空非负整数：服务端可能返回 null 表示"尚未统计"。 */
function readOptionalCount(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  const value = readOptionalNumber(record, key, path);
  if (value === null) {
    return null;
  }
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${path} 必须是非负整数或 null`);
  }
  return value;
}

const ACTIONS = ["start", "succeed", "fail", "cancel"] as const;

export function parseDataSource(value: unknown): DataSource {
  const record = asRecord(value, "数据源");
  const revision = readString(record, "revision", "revision");
  // 服务端契约要求 40 位 sha；前端也校验，避免异常响应被当成可用数据渲染。
  if (!/^[0-9a-f]{40}$/.test(revision)) {
    throw new Error("revision 必须是 40 位 commit sha");
  }
  const isGated = readBoolean(record, "is_gated", "is_gated");
  const accessCondition = readEnum(record, "access_condition", "access_condition", ACCESS);
  if (accessCondition === "GATED" && !isGated) {
    throw new Error("access_condition=GATED 与 is_gated 不一致");
  }
  return {
    data_source_id: readUuid(record, "data_source_id", "data_source_id"),
    project_id: readUuid(record, "project_id", "project_id"),
    code: readString(record, "code", "code"),
    name: readString(record, "name", "name"),
    kind: readEnum(record, "kind", "kind", KINDS),
    repo_id: readString(record, "repo_id", "repo_id"),
    revision,
    license: readString(record, "license", "license"),
    access_condition: accessCondition,
    is_gated: isGated,
    config_name: readOptionalString(record, "config_name", "config_name"),
    split: readOptionalString(record, "split", "split"),
    dataset_card_url: readOptionalString(
      record,
      "dataset_card_url",
      "dataset_card_url",
    ),
    notes: readOptionalString(record, "notes", "notes"),
    metadata: (record.metadata as Record<string, unknown>) ?? {},
    version: readPositiveInteger(record, "version", "version"),
  };
}

export function parseDataSourceImport(value: unknown): DataSourceImport {
  const record = asRecord(value, "导入任务");
  const rawActions = record.allowed_actions;
  if (!Array.isArray(rawActions)) {
    throw new Error("allowed_actions 必须是数组");
  }
  const allowed = rawActions.map((item, index) =>
    readEnum(
      { action: item },
      "action",
      `allowed_actions[${index}]`,
      ACTIONS,
    ),
  );
  return {
    import_id: readUuid(record, "import_id", "import_id"),
    project_id: readUuid(record, "project_id", "project_id"),
    data_source_id: readUuid(record, "data_source_id", "data_source_id"),
    status: readEnum(record, "status", "status", STATUSES),
    scope: readEnum(record, "scope", "scope", SCOPES),
    tool_name: readString(record, "tool_name", "tool_name"),
    tool_version: readString(record, "tool_version", "tool_version"),
    sample_limit: readOptionalCount(record, "sample_limit", "sample_limit"),
    cache_location: readOptionalString(
      record,
      "cache_location",
      "cache_location",
    ),
    imported_row_count: readOptionalCount(
      record,
      "imported_row_count",
      "imported_row_count",
    ),
    imported_file_count: readOptionalCount(
      record,
      "imported_file_count",
      "imported_file_count",
    ),
    error_message: readOptionalString(record, "error_message", "error_message"),
    started_at: readOptionalString(record, "started_at", "started_at"),
    finished_at: readOptionalString(record, "finished_at", "finished_at"),
    version: readPositiveInteger(record, "version", "version"),
    allowed_actions: allowed,
  };
}

/** 供测试与展示使用的整数读取，保持与其他解析器一致的错误口径。 */
export { readInteger };
