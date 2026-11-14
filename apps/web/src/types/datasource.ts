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
