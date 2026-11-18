/** 数据源与导入任务的严格响应解析器。 */

import type {
  AccessCondition,
  DataReview,
  DataSource,
  DataSourceDetail,
  DataSourceImport,
  DataSourceKind,
  ImportLog,
  ImportLogLevel,
  ImportLogList,
  ImportScope,
  ImportStage,
  ImportStatus,
  MediaAsset,
  MediaPlayback,
  QualityFinding,
  QualityReport,
  RemoteCheck,
  ReviewDecision,
  ReviewEvidence,
  ReviewStatus,
} from "../types/datasource";
import {
  asRecord,
  readBoolean,
  readEnum,
  readNumber,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readOptionalUuid,
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
const STAGES: readonly ImportStage[] = [
  "DOWNLOAD",
  "VALIDATE",
  "PARSE",
  "INGEST",
  "COMPLETE",
];
const LOG_LEVELS: readonly ImportLogLevel[] = ["INFO", "WARNING", "ERROR"];

/** 可空阶段：null 表示"未测量"，不是缺失字段。 */
function readOptionalStage(
  record: Record<string, unknown>,
  key: string,
  path: string,
): ImportStage | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readEnum(record, key, path, STAGES);
}

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
const REVIEW_STATUSES: readonly ReviewStatus[] = [
  "PENDING",
  "APPROVED",
  "REJECTED",
];
const REVIEW_DECISIONS: readonly ReviewDecision[] = [
  "ACCEPT",
  "REJECT",
  "NEEDS_FIX",
];

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
  const result: DataSourceImport = {
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
    stage: readOptionalStage(record, "stage", "stage"),
    processed_items: readOptionalCount(
      record,
      "processed_items",
      "processed_items",
    ),
    total_items: readOptionalCount(record, "total_items", "total_items"),
    version: readPositiveInteger(record, "version", "version"),
    allowed_actions: allowed,
  };
  if (
    result.processed_items !== null &&
    result.total_items !== null &&
    result.processed_items > result.total_items
  ) {
    throw new Error("processed_items 不得超过 total_items");
  }
  return result;
}

const REMOTE_CHECK_STATUSES = ["VERIFIED"] as const;

export function parseRemoteCheck(value: unknown, path = "远端校验"): RemoteCheck {
  const record = asRecord(value, path);
  const checkedAt = readString(record, "checked_at", `${path}.checked_at`);
  if (Number.isNaN(Date.parse(checkedAt))) {
    throw new Error(`${path}.checked_at 必须是可解析的时间`);
  }
  const resolvedRevision = readString(
    record,
    "resolved_revision",
    `${path}.resolved_revision`,
  );
  if (!/^[0-9a-f]{40}$/.test(resolvedRevision)) {
    throw new Error(`${path}.resolved_revision 必须是 40 位 commit sha`);
  }
  return {
    status: readEnum(record, "status", `${path}.status`, REMOTE_CHECK_STATUSES),
    checked_at: checkedAt,
    resolved_revision: resolvedRevision,
    license: readOptionalString(record, "license", `${path}.license`),
    is_gated: readBoolean(record, "is_gated", `${path}.is_gated`),
    dataset_card_url: readOptionalString(
      record,
      "dataset_card_url",
      `${path}.dataset_card_url`,
    ),
  };
}

/**
 * 数据源详情聚合。
 *
 * 严格校验导入任务确实属于该数据源：服务端若把跨数据源的任务混进详情，
 * 前端必须拒绝渲染，而不是把错配数据当成本次导入历史展示。
 */
export function parseDataSourceDetail(value: unknown): DataSourceDetail {
  const record = asRecord(value, "数据源详情");
  const dataSource = parseDataSource(record.data_source);
  if (!Array.isArray(record.imports)) {
    throw new Error("数据源详情.imports 必须是数组");
  }
  const imports = record.imports.map((item, index) => {
    const parsed = parseDataSourceImport(item);
    if (parsed.data_source_id !== dataSource.data_source_id) {
      throw new Error(
        `数据源详情.imports[${index}].data_source_id 与数据源不一致`,
      );
    }
    return parsed;
  });
  const rawRemoteCheck = record.remote_check;
  return {
    data_source: dataSource,
    imports,
    remote_check:
      rawRemoteCheck === null || rawRemoteCheck === undefined
        ? null
        : parseRemoteCheck(rawRemoteCheck, "数据源详情.remote_check"),
  };
}

function parseImportLog(value: unknown, path: string): ImportLog {
  const record = asRecord(value, path);
  const message = readString(record, "message", `${path}.message`);
  if (message.trim().length === 0) {
    throw new Error(`${path}.message 不能为空`);
  }
  return {
    log_id: readUuid(record, "log_id", `${path}.log_id`),
    project_id: readUuid(record, "project_id", `${path}.project_id`),
    import_id: readUuid(record, "import_id", `${path}.import_id`),
    stage: readEnum(record, "stage", `${path}.stage`, STAGES),
    level: readEnum(record, "level", `${path}.level`, LOG_LEVELS),
    message,
    attempt: readPositiveInteger(record, "attempt", `${path}.attempt`),
    occurred_at: readString(record, "occurred_at", `${path}.occurred_at`),
  };
}

/** 步骤日志列表：`truncated` 必须显式给出，不能把"没返回"当成"没有更多"。 */
export function parseImportLogList(value: unknown): ImportLogList {
  const record = asRecord(value, "导入日志列表");
  if (!Array.isArray(record.items)) {
    throw new Error("导入日志列表.items 必须是数组");
  }
  const truncated = readBoolean(record, "truncated", "导入日志列表.truncated");
  const nextCursor = readOptionalString(
    record,
    "next_cursor",
    "导入日志列表.next_cursor",
  );
  // 契约规定两个字段表达同一事实；不一致说明服务端语义漂移，
  // 继续渲染会让"加载更多"按钮的状态与 truncated 提示互相矛盾。
  if (truncated !== (nextCursor !== null)) {
    throw new Error(
      "导入日志列表.truncated 与 next_cursor 语义不一致：truncated 必须等价于 next_cursor !== null",
    );
  }
  return {
    items: record.items.map((item, index) =>
      parseImportLog(item, `导入日志列表.items[${index}]`),
    ),
    truncated,
    next_cursor: nextCursor,
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

const REVIEW_EVIDENCE_KEYS = new Set([
  "timestamp_seconds",
  "camera",
  "note",
  "episode_key",
]);

/** 服务端只写白名单键；未知键说明契约漂移，按校验失败处理而不是静默丢弃。 */
export function parseReviewEvidence(
  value: unknown,
  path: string,
): ReviewEvidence | null {
  if (value === undefined || value === null) {
    return null;
  }
  const record = asRecord(value, path);
  for (const key of Object.keys(record)) {
    if (!REVIEW_EVIDENCE_KEYS.has(key)) {
      throw new Error(`${path}.${key} 不是允许的审核证据键`);
    }
  }
  const timestamp = readOptionalNumber(
    record,
    "timestamp_seconds",
    `${path}.timestamp_seconds`,
  );
  if (timestamp !== null && timestamp < 0) {
    throw new Error(`${path}.timestamp_seconds 必须是非负数`);
  }
  return {
    timestamp_seconds: timestamp,
    camera: readOptionalString(record, "camera", `${path}.camera`),
    note: readOptionalString(record, "note", `${path}.note`),
    episode_key: readOptionalString(
      record,
      "episode_key",
      `${path}.episode_key`,
    ),
  };
}

/** 服务端保证职责分离：审核人不会等于提交人，前端只如实呈现。 */
export function parseDataReview(value: unknown): DataReview {
  const record = asRecord(value, "审核记录");
  return {
    review_id: readUuid(record, "review_id", "审核记录.review_id"),
    project_id: readUuid(record, "project_id", "审核记录.project_id"),
    import_id: readUuid(record, "import_id", "审核记录.import_id"),
    status: readEnum(
      record,
      "status",
      "审核记录.status",
      REVIEW_STATUSES,
    ),
    decision:
      record.decision === null || record.decision === undefined
        ? null
        : readEnum(record, "decision", "审核记录.decision", REVIEW_DECISIONS),
    reason: readOptionalString(record, "reason", "审核记录.reason"),
    reviewer_id: readOptionalUuid(
      record,
      "reviewer_id",
      "审核记录.reviewer_id",
    ),
    submitted_by: readUuid(record, "submitted_by", "审核记录.submitted_by"),
    created_at: readString(record, "created_at", "审核记录.created_at"),
    decided_at: readOptionalString(
      record,
      "decided_at",
      "审核记录.decided_at",
    ),
    version: readPositiveInteger(record, "version", "审核记录.version"),
    evidence: parseReviewEvidence(record.evidence, "审核记录.evidence"),
  };
}

function parseFinding(value: unknown, path: string): QualityFinding {
  const record = asRecord(value, path);
  return {
    rule_id: readString(record, "rule_id", `${path}.rule_id`),
    passed: readBoolean(record, "passed", `${path}.passed`),
    detail: readString(record, "detail", `${path}.detail`),
  };
}

function parseFindings(
  record: Record<string, unknown>,
  key: string,
  path: string,
): QualityFinding[] {
  const raw = record[key];
  if (!Array.isArray(raw)) {
    throw new Error(`${path} 必须是数组`);
  }
  return raw.map((item, index) => parseFinding(item, `${path}[${index}]`));
}

/** 质量报告按规则版本返回；`passed` 是服务端结论，前端不再自行判定。 */
export function parseQualityReport(value: unknown): QualityReport {
  const record = asRecord(value, "质量报告");
  return {
    import_id: readUuid(record, "import_id", "质量报告.import_id"),
    rule_version: readString(record, "rule_version", "质量报告.rule_version"),
    sample_scope: readString(record, "sample_scope", "质量报告.sample_scope"),
    total_assets: readInteger(record, "total_assets", "质量报告.total_assets"),
    total_episodes: readInteger(
      record,
      "total_episodes",
      "质量报告.total_episodes",
    ),
    passed: readBoolean(record, "passed", "质量报告.passed"),
    findings: parseFindings(record, "findings", "质量报告.findings"),
    failures: parseFindings(record, "failures", "质量报告.failures"),
  };
}
