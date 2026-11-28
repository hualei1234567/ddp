/**
 * 处理运行（processing-runs）领域类型。
 *
 * 字段与后端 `backend/app/api/v1/processing_response_models.py` 的
 * `ProcessingRunOut` / `ProcessingRunStepOut` 一一对应；`steps` 只在详情
 * 响应内联，列表响应为 `null`。`allowed_actions` 是服务端根据状态机算出的
 * 权威值，前端只按它渲染按钮。
 */

export interface ProcessingRunStep {
  step_id: string;
  run_id: string;
  stage: string;
  attempt: number;
  status: string;
  log_summary: string | null;
  error_code: string | null;
  error_summary: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProcessingRun {
  run_id: string;
  project_id: string;
  pipeline_version: string;
  input_refs: string[];
  output_refs: string[];
  status: string;
  stage: string | null;
  attempt: number;
  retry_count: number;
  error_code: string | null;
  error_summary: string | null;
  started_at: string | null;
  finished_at: string | null;
  closed_at: string | null;
  allowed_actions: string[];
  version: number;
  created_at: string;
  updated_at: string;
  steps: ProcessingRunStep[] | null;
}
