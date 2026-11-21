/**
 * 标注周期工作量前端类型（V31-026，只读）。
 *
 * 字段与服务端 `AnnotationWorkloadOut` 一致。`capacity_minutes` 与
 * `utilization_ratio` 为 `null` 表示「容量未配置」，**不是 0**——0 是真实的
 * "零容量/零利用率"，把两者混同会让运营页把未配置当作已超载。
 */

export const WORKLOAD_STATUS_VALUES = [
  "NORMAL",
  "AT_CAPACITY",
  "OVER_CAPACITY",
  "STALE",
  "PARTIAL",
  "UNKNOWN",
] as const;
export type WorkloadStatus = (typeof WORKLOAD_STATUS_VALUES)[number];

export const MISSING_SEMANTICS_VALUES = [
  "NONE",
  "N/A",
  "UNKNOWN",
  "STALE",
  "PARTIAL",
] as const;
export type MissingSemantics = (typeof MISSING_SEMANTICS_VALUES)[number];

export interface AnnotationWorkload {
  project_id: string;
  user_id: string;
  period_start: string;
  period_end: string;
  assigned_item_count: number;
  claimed_item_count: number;
  submitted_item_count: number;
  approved_item_count: number;
  rework_item_count: number;
  total_minutes: number;
  /** 部署配置的周期容量；null 表示未配置。 */
  capacity_minutes: number | null;
  /** total_minutes / capacity_minutes；容量未配置或为 0 时为 null。 */
  utilization_ratio: number | null;
  status: WorkloadStatus;
  missing_semantics: MissingSemantics;
  /** 聚合口径版本，例如 `annotation-assignment-events/1`。 */
  source_version: string;
}
