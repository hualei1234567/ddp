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
