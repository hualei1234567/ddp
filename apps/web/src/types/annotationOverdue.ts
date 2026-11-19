/**
 * 标注逾期汇总前端类型（N6-002 收尾，只读）。
 *
 * 字段与服务端 `AnnotationOverdueSummaryOut` 一致；外部数据进入 UI 前必须由
 * `services/annotationOverdueParsers.ts` 严格解析。分桶边界由服务端回传，
 * 前端不得硬编码 24h / 72h。
 */
import type { AnnotationTaskStatus, TaskPriority } from "./annotation";

export const OVERDUE_BUCKET_VALUES = ["LT_24H", "D1_TO_D3", "GT_3D"] as const;
export type OverdueBucket = (typeof OVERDUE_BUCKET_VALUES)[number];

export interface OverduePriorityCount {
  priority: TaskPriority;
  count: number;
}

export interface OverdueStatusCount {
  status: AnnotationTaskStatus;
  count: number;
}

export interface OverdueBucketCount {
  bucket: OverdueBucket;
  count: number;
  /** 分桶下界（小时，含）。 */
  lower_bound_hours: number;
  /** 分桶上界（小时，不含）；null 表示无上界。 */
  upper_bound_hours: number | null;
}

export interface AnnotationOverdueSummary {
  project_id: string;
  /** 服务端计算分桶所用的“现在”，客户端据此解释边界。 */
  evaluated_at: string;
  total_overdue: number;
  /** 逾期任务里最早的 due_at；没有逾期任务时为 null。 */
  earliest_due_at: string | null;
  by_priority: OverduePriorityCount[];
  /** 只列出实际出现过的状态；计数为 0 的状态不占位。 */
  by_status: OverdueStatusCount[];
  buckets: OverdueBucketCount[];
}
