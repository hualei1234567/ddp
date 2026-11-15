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

export interface DataSourceImport {
  import_id: string;
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
  version: number;
  /** 服务端计算的可执行动作；前端只按此渲染按钮，不自行推断。 */
  allowed_actions: ImportAction[];
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
