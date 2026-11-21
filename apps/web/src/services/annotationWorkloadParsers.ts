/**
 * 标注周期工作量解析（V31-026）。
 *
 * 严格解析：字段缺失、类型不符、枚举越界一律 `ContractValidationError`，
 * 不把 `null` 容量降级成 0，也不给未知状态兜底成 `NORMAL`——"未配置"
 * 与"未超容量"是两件事。
 */
import type {
  AnnotationWorkload,
  MissingSemantics,
  WorkloadStatus,
} from "../types/annotationWorkload";
import {
  MISSING_SEMANTICS_VALUES,
  WORKLOAD_STATUS_VALUES,
} from "../types/annotationWorkload";
import {
  asRecord,
  ContractValidationError,
  readEnum,
  readInteger,
  readOptionalNumber,
  readString,
  readUuid,
} from "./validation";

function readOptionalInteger(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  const value = readOptionalNumber(record, key, path);
  if (value === null) {
    return null;
  }
  if (!Number.isInteger(value) || value < 0) {
    throw new ContractValidationError(`${path} 必须是非负整数或 null`);
  }
  return value;
}

export function parseAnnotationWorkload(value: unknown): AnnotationWorkload {
  const record = asRecord(value, "工作量汇总");
  const capacity = readOptionalInteger(
    record,
    "capacity_minutes",
    "工作量汇总.capacity_minutes",
  );
  const utilization = readOptionalNumber(
    record,
    "utilization_ratio",
    "工作量汇总.utilization_ratio",
  );
  // 利用率与容量必须同时有值或同时为空：只有其一说明服务端口径被破坏。
  if ((capacity === null) !== (utilization === null)) {
    throw new ContractValidationError(
      "工作量汇总.capacity_minutes 与 utilization_ratio 必须同时为空或同时有值",
    );
  }
  return {
    project_id: readUuid(record, "project_id", "工作量汇总.project_id"),
    user_id: readUuid(record, "user_id", "工作量汇总.user_id"),
    period_start: readString(record, "period_start", "工作量汇总.period_start"),
    period_end: readString(record, "period_end", "工作量汇总.period_end"),
    assigned_item_count: readInteger(
      record,
      "assigned_item_count",
      "工作量汇总.assigned_item_count",
    ),
    claimed_item_count: readInteger(
      record,
      "claimed_item_count",
      "工作量汇总.claimed_item_count",
    ),
    submitted_item_count: readInteger(
      record,
      "submitted_item_count",
      "工作量汇总.submitted_item_count",
    ),
    approved_item_count: readInteger(
      record,
      "approved_item_count",
      "工作量汇总.approved_item_count",
    ),
    rework_item_count: readInteger(
      record,
      "rework_item_count",
      "工作量汇总.rework_item_count",
    ),
    total_minutes: readInteger(record, "total_minutes", "工作量汇总.total_minutes"),
    capacity_minutes: capacity,
    utilization_ratio: utilization,
    status: readEnum<WorkloadStatus>(
      record,
      "status",
      "工作量汇总.status",
      WORKLOAD_STATUS_VALUES,
    ),
    missing_semantics: readEnum<MissingSemantics>(
      record,
      "missing_semantics",
      "工作量汇总.missing_semantics",
      MISSING_SEMANTICS_VALUES,
    ),
    source_version: readString(
      record,
      "source_version",
      "工作量汇总.source_version",
    ),
  };
}
