/** 阶段 7 数据平面：外部数据源与导入任务。 */

export type DataSourceKind = "HUGGINGFACE";
export type AccessCondition = "PUBLIC" | "GATED" | "PRIVATE" | "UNKNOWN";
export type ImportScope = "SAMPLE" | "SPLIT" | "FULL";
export type ImportStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";
export type ImportAction = "start" | "succeed" | "fail" | "cancel";
/** 导入执行阶段；`null`（不是空字符串）表示未测量。 */
export type ImportStage =
  | "DOWNLOAD"
  | "VALIDATE"
  | "PARSE"
  | "INGEST"
  | "COMPLETE";
export type ImportLogLevel = "INFO" | "WARNING" | "ERROR";

export interface DataSource {
  data_source_id: string;
  project_id: string;
  code: string;
  name: string;
  kind: DataSourceKind;
  repo_id: string;
  /** 40 位 commit sha；服务端拒绝分支名。 */
  revision: string;
  license: string;
  access_condition: AccessCondition;
  is_gated: boolean;
  config_name: string | null;
  split: string | null;
  dataset_card_url: string | null;
  notes: string | null;
  metadata: Record<string, unknown>;
  version: number;
}

/** 服务端记录的一次远端校验结论；只含 Adapter 已规范化的字段。 */
export interface RemoteCheck {
  status: "VERIFIED";
  checked_at: string;
  /** 远端解析出的不可变 commit sha。 */
  resolved_revision: string;
  /** Dataset Card 声明的许可证；null 表示远端未声明，不是"无限制"。 */
  license: string | null;
  is_gated: boolean;
  dataset_card_url: string | null;
}

/** 数据源详情聚合：登记信息 + 导入任务 + 最近校验结论。 */
export interface DataSourceDetail {
  data_source: DataSource;
  imports: DataSourceImport[];
  /** null 表示从未校验或摘要损坏；界面必须显示"未校验"。 */
  remote_check: RemoteCheck | null;
}

export interface DataSourceImport {  import_id: string;
  project_id: string;
  data_source_id: string;
  status: ImportStatus;
  scope: ImportScope;
  tool_name: string;
  tool_version: string;
  sample_limit: number | null;
  cache_location: string | null;
  imported_row_count: number | null;
  imported_file_count: number | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  /**
   * 执行阶段。`null` = 未测量（迁移 0055 前的存量导入），
   * 前端必须显示"未测量"而不是默认成 DOWNLOAD 或 0%。
   */
  stage: ImportStage | null;
  /** 已处理的媒体文件数；与 `total_items` 同为 null 时不展示百分比。 */
  processed_items: number | null;
  total_items: number | null;
  version: number;
  /** 服务端计算的可执行动作；前端只按此渲染按钮，不自行推断。 */
  allowed_actions: ImportAction[];
}

/** 一条导入步骤日志（消息已由服务端脱敏）。 */
export interface ImportLog {
  log_id: string;
  project_id: string;
  import_id: string;
  stage: ImportStage;
  level: ImportLogLevel;
  message: string;
  /** 第几次尝试产生的日志；重试后可区分轮次。 */
  attempt: number;
  occurred_at: string;
}

export interface ImportLogList {
  items: ImportLog[];
  /**
   * true 当且仅当还有更早的日志未返回（等价于 `next_cursor !== null`）。
   * 不表示消息体被截断——消息脱敏长度是独立规则。
   */
  truncated: boolean;
  /** 下一页（更早日志）的游标；null 表示已经到最早一条。 */
  next_cursor: string | null;
}

/** 导入产出的一个机位视频。 */
export interface MediaAsset {
  asset_id: string;
  project_id: string;
  data_source_id: string;
  import_id: string;
  episode_key: string;
  path: string;
  camera: string;
  object_key: string;
  size_bytes: number;
  sha256: string;
  content_type: string;
  task_name: string;
  excerpt_start_seconds: number | null;
  excerpt_seconds: number | null;
  source_fps: number | null;
  created_at: string;
}

/** 预签名播放信息；URL 由服务端签发，前端不拼接存储路径。 */
export interface MediaPlayback {
  asset_id: string;
  url: string;
  expires_in_seconds: number;
  content_type: string;
  size_bytes: number;
}

export type ReviewStatus = "PENDING" | "APPROVED" | "REJECTED";
export type ReviewDecision = "ACCEPT" | "REJECT" | "NEEDS_FIX";

/**
 * 结构化审核依据：时间点、机位、备注与片段键。
 * 服务端只接受白名单键并限制总字节数；前端表单只生成这四类字段。
 */
export interface ReviewEvidence {
  timestamp_seconds?: number | null;
  camera?: string | null;
  note?: string | null;
  episode_key?: string | null;
}

/**
 * 导入结果的人工审核记录。
 *
 * `reviewer_id` 与 `submitted_by` 分开返回，是为了让前端能显示"谁提交、谁审核"——
 * 职责分离由服务端与数据库约束强制，前端只负责如实呈现。
 */
export interface DataReview {
  review_id: string;
  project_id: string;
  import_id: string;
  status: ReviewStatus;
  decision: ReviewDecision | null;
  reason: string | null;
  reviewer_id: string | null;
  submitted_by: string;
  created_at: string;
  decided_at: string | null;
  version: number;
  /** 旧审核记录为 null；服务端只接受白名单键的结构化对象。 */
  evidence: ReviewEvidence | null;
}

/** 单条质量规则的结果；`passed=false` 时 `detail` 说明原因。 */
export interface QualityFinding {
  rule_id: string;
  passed: boolean;
  detail: string;
}

/**
 * 质量报告：规则版本随报告返回，便于解释"为什么当时通过"。
 * 服务端已按规则版本重算，前端不重新判定。
 */
export interface QualityReport {
  import_id: string;
  rule_version: string;
  sample_scope: string;
  total_assets: number;
  total_episodes: number;
  passed: boolean;
  findings: QualityFinding[];
  failures: QualityFinding[];
}
