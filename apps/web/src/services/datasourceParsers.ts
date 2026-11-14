/** 数据源与导入任务的严格响应解析器。 */

import type {
  AccessCondition,
  DataSource,
  DataSourceImport,
  DataSourceKind,
  ImportScope,
  ImportStatus,
  MediaAsset,
  MediaPlayback,
} from "../types/datasource";
import {
  asRecord,
  readBoolean,
  readEnum,
  readNumber,
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

/**
 * 已知机位。**不用于校验**：服务端可能返回新机位（保留原文归一成 slug），
 * 前端不应因"不认识"就丢数据；这里只用于展示层做类型提示。
 */
export type KnownCamera =
  | "main"
  | "right"
  | "stereo"
  | "wrist_left"
  | "wrist_right";

function readNullableFloat(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  return readOptionalNumber(record, key, path);
}

export function parseMediaAsset(value: unknown): MediaAsset {
  const record = asRecord(value, "媒体资产");
  const camera = readString(record, "camera", "camera");
  const sha256 = readString(record, "sha256", "sha256");
  if (sha256.length !== 71 || !sha256.startsWith("sha256:")) {
    throw new Error("sha256 必须是 'sha256:' + 64 位十六进制");
  }
  const sizeBytes = readInteger(record, "size_bytes", "size_bytes");
  if (sizeBytes <= 0) {
    throw new Error("size_bytes 必须大于 0");
  }
  return {
    asset_id: readUuid(record, "asset_id", "asset_id"),
    project_id: readUuid(record, "project_id", "project_id"),
    data_source_id: readUuid(record, "data_source_id", "data_source_id"),
    import_id: readUuid(record, "import_id", "import_id"),
    episode_key: readString(record, "episode_key", "episode_key"),
    path: readString(record, "path", "path"),
    // 机位是开放的：服务端可能返回未知机位（保留原文归一成 slug），
    // 前端不因"不认识"就丢数据。
    camera,
    object_key: readString(record, "object_key", "object_key"),
    size_bytes: sizeBytes,
    sha256,
    content_type: readString(record, "content_type", "content_type"),
    task_name: readString(record, "task_name", "task_name"),
    excerpt_start_seconds: readNullableFloat(
      record,
      "excerpt_start_seconds",
      "excerpt_start_seconds",
    ),
    excerpt_seconds: readNullableFloat(
      record,
      "excerpt_seconds",
      "excerpt_seconds",
    ),
    source_fps: readNullableFloat(record, "source_fps", "source_fps"),
    created_at: readString(record, "created_at", "created_at"),
  };
}

export function parseMediaPlayback(value: unknown): MediaPlayback {
  const record = asRecord(value, "播放信息");
  const url = readString(record, "url", "url");
  // 必须是 http(s) 且带签名参数；避免把空串或相对路径当成可播放地址。
  if (!/^https?:\/\//.test(url)) {
    throw new Error("播放 URL 必须是 http(s) 绝对地址");
  }
  if (!url.includes("X-Amz-Signature=")) {
    throw new Error("播放 URL 缺少签名参数");
  }
  return {
    asset_id: readUuid(record, "asset_id", "asset_id"),
    url,
    expires_in_seconds: readInteger(
      record,
      "expires_in_seconds",
      "expires_in_seconds",
    ),
    content_type: readString(record, "content_type", "content_type"),
    size_bytes: readNumber(record, "size_bytes", "size_bytes"),
  };
}

/** 机位的展示名；未知机位原样展示，不隐藏。 */
export function presentCamera(camera: string): string {
  const labels: Record<string, string> = {
    main: "主视角",
    right: "右视角",
    stereo: "立体",
    wrist_left: "左腕",
    wrist_right: "右腕",
  };
  return labels[camera] ?? camera;
}
