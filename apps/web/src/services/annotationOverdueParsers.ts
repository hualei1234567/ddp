/**
 * 标注逾期汇总解析（N6-002 收尾）。
 *
 * 为什么单独成文件：`annotationParsers.ts` 已超过 500 行评审线；汇总对象与任务
 * 实体无关，独立解析不撑大既有文件。所有字段严格校验，服务端新增/缺字段
 * 一律报 `ContractValidationError`，不静默降级成 0（0 是真实的"没有逾期"，
 * 不能与"解析失败"混同）。
 */
import type {
  AnnotationOverdueSummary,
  OverdueBucketCount,
  OverduePriorityCount,
  OverdueStatusCount,
} from "../types/annotationOverdue";
import {
  ANNOTATION_TASK_STATUS_VALUES,
  TASK_PRIORITY_VALUES,
} from "../types/annotation";
import { OVERDUE_BUCKET_VALUES } from "../types/annotationOverdue";
import {
  asRecord,
  ContractValidationError,
  readEnum,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readString,
  readUuid,
} from "./validation";

function readArray(
  record: Record<string, unknown>,
  key: string,
  path: string,
): unknown[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    throw new ContractValidationError(`${path} 必须是数组`);
  }
  return value;
}

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

function parsePriorityCount(value: unknown, path: string): OverduePriorityCount {
  const record = asRecord(value, path);
  return {
    priority: readEnum(record, "priority", `${path}.priority`, TASK_PRIORITY_VALUES),
    count: readInteger(record, "count", `${path}.count`),
  };
}

function parseStatusCount(value: unknown, path: string): OverdueStatusCount {
  const record = asRecord(value, path);
  return {
    status: readEnum(
      record,
      "status",
      `${path}.status`,
      ANNOTATION_TASK_STATUS_VALUES,
    ),
    count: readInteger(record, "count", `${path}.count`),
  };
}

function parseBucketCount(value: unknown, path: string): OverdueBucketCount {
  const record = asRecord(value, path);
  return {
    bucket: readEnum(record, "bucket", `${path}.bucket`, OVERDUE_BUCKET_VALUES),
    count: readInteger(record, "count", `${path}.count`),
    lower_bound_hours: readInteger(
      record,
      "lower_bound_hours",
      `${path}.lower_bound_hours`,
    ),
    upper_bound_hours: readOptionalInteger(
      record,
      "upper_bound_hours",
      `${path}.upper_bound_hours`,
    ),
  };
}

export function parseAnnotationOverdueSummary(
  value: unknown,
): AnnotationOverdueSummary {
  const record = asRecord(value, "逾期汇总");
  const summary: AnnotationOverdueSummary = {
    project_id: readUuid(record, "project_id", "逾期汇总.project_id"),
    evaluated_at: readString(record, "evaluated_at", "逾期汇总.evaluated_at"),
    total_overdue: readInteger(record, "total_overdue", "逾期汇总.total_overdue"),
    earliest_due_at: readOptionalString(
      record,
      "earliest_due_at",
      "逾期汇总.earliest_due_at",
    ),
    by_priority: readArray(record, "by_priority", "逾期汇总.by_priority").map(
      (item, index) =>
        parsePriorityCount(item, `逾期汇总.by_priority[${index}]`),
    ),
    by_status: readArray(record, "by_status", "逾期汇总.by_status").map(
      (item, index) => parseStatusCount(item, `逾期汇总.by_status[${index}]`),
    ),
    buckets: readArray(record, "buckets", "逾期汇总.buckets").map(
      (item, index) => parseBucketCount(item, `逾期汇总.buckets[${index}]`),
    ),
  };
  // 服务端保证三个分布之和等于总数；不一致说明契约被破坏，不能当成 UI 小误差。
  const sum = (items: { count: number }[]): number =>
    items.reduce((total, item) => total + item.count, 0);
  if (
    sum(summary.by_priority) !== summary.total_overdue ||
    sum(summary.by_status) !== summary.total_overdue ||
    sum(summary.buckets) !== summary.total_overdue
  ) {
    throw new ContractValidationError("逾期汇总各分布之和与 total_overdue 不一致");
  }
  return summary;
}
