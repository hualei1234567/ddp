/** 处理运行（processing-runs）响应解析与分页。 */

import type { ProcessingRun, ProcessingRunStep } from "../types/processing";
import {
  asRecord,
  ContractValidationError,
  parseCursorPage,
  readInteger,
  readOptionalString,
  readString,
  readStringArray,
  readUuid,
} from "./validation";

/**
 * 解析单个处理运行。
 *
 * 服务端对未知枚举的容忍策略是"扩展不破坏"：状态 / 阶段可能随后端升级
 * 新增取值，这里只校验是字符串，展示层再负责原样呈现。
 */
export function parseProcessingRun(value: unknown, path = "processingRun"): ProcessingRun {
  const record = asRecord(value, path);
  return {
    run_id: readUuid(record, "run_id", path),
    project_id: readUuid(record, "project_id", path),
    pipeline_version: readString(record, "pipeline_version", path),
    input_refs: readStringArray(record, "input_refs", path),
    output_refs: readStringArray(record, "output_refs", path),
    status: readString(record, "status", path),
    stage: readOptionalString(record, "stage", path),
    attempt: readInteger(record, "attempt", path),
    retry_count: readInteger(record, "retry_count", path),
    error_code: readOptionalString(record, "error_code", path),
    error_summary: readOptionalString(record, "error_summary", path),
    started_at: readOptionalString(record, "started_at", path),
    finished_at: readOptionalString(record, "finished_at", path),
    closed_at: readOptionalString(record, "closed_at", path),
    allowed_actions: readStringArray(record, "allowed_actions", path),
    version: readInteger(record, "version", path),
    created_at: readString(record, "created_at", path),
    updated_at: readString(record, "updated_at", path),
    steps: parseOptionalSteps(record.steps, path),
  };
}

function parseOptionalSteps(
  value: unknown,
  path: string,
): ProcessingRunStep[] | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path}.steps 必须是数组或 null`);
  }
  return value.map((item, index) =>
    parseProcessingRunStep(item, `${path}.steps[${index}]`),
  );
}

export function parseProcessingRunStep(
  value: unknown,
  path = "processingRunStep",
): ProcessingRunStep {
  const record = asRecord(value, path);
  return {
    step_id: readUuid(record, "step_id", path),
    run_id: readUuid(record, "run_id", path),
    stage: readString(record, "stage", path),
    attempt: readInteger(record, "attempt", path),
    status: readString(record, "status", path),
    log_summary: readOptionalString(record, "log_summary", path),
    error_code: readOptionalString(record, "error_code", path),
    error_summary: readOptionalString(record, "error_summary", path),
    started_at: readString(record, "started_at", path),
    finished_at: readOptionalString(record, "finished_at", path),
    created_at: readString(record, "created_at", path),
    updated_at: readString(record, "updated_at", path),
  };
}

export function parseProcessingRunPage(value: unknown) {
  return parseCursorPage(value, "processingRun", parseProcessingRun);
}

export function parseProcessingRunStepsPage(value: unknown) {
  return parseCursorPage(value, "processingRunStep", parseProcessingRunStep);
}
