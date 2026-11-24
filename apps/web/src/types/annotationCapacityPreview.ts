/**
 * 分配前实时负载提示的前端类型（FR-020，只读预览）。
 *
 * 字段与服务端 `AnnotationAssignmentCapacityPreview` 一致。
 *
 * 语义红线：
 * - `max_*` 为 `null` 表示**部署未配置该上限**，不是 0——渲染成 0 会让
 *   "没配上限"被画成"已经超载"；
 * - `will_exceed` 是服务端派生结论，与提交时的 409 同源。前端**只提示不阻断**：
 *   是否放行由既有 `CapacityPolicy` 决定，本层不得自行拦截提交；
 * - 这是**提示**不是**自动均衡**：不提供"帮我选一个负载低的人"之类的结论。
 */

export const CAPACITY_PREVIEW_STATUS_VALUES = [
  "NORMAL",
  "AT_CAPACITY",
  "OVER_CAPACITY",
] as const;
export type CapacityPreviewStatus =
  (typeof CAPACITY_PREVIEW_STATUS_VALUES)[number];

export interface AssignmentCapacityPreview {
  assignee_id: string;
  /** 当前进行中分配的计划项数之和。 */
  open_item_count: number;
  /** 当前进行中分配的计划工时之和（分钟）。 */
  open_planned_minutes: number;
  /** 本次拟分配项数（服务端回显）。 */
  planned_item_count: number;
  /** 本次拟分配工时（分钟，服务端回显）。 */
  planned_minutes: number;
  projected_item_count: number;
  projected_planned_minutes: number;
  /** 部署配置的进行中项数上限；null 表示未配置，不是 0。 */
  max_open_items: number | null;
  /** 部署配置的进行中计划工时上限；null 表示未配置，不是 0。 */
  max_planned_minutes: number | null;
  item_capacity_ratio: number | null;
  minutes_capacity_ratio: number | null;
  will_exceed: boolean;
  exceeds_open_items: boolean;
  exceeds_planned_minutes: boolean;
  status: CapacityPreviewStatus;
}

/** 预览请求：最多 50 人，与契约 `maxItems` 一致。 */
export interface AssignmentCapacityPreviewRequest {
  assignee_ids: string[];
  planned_item_count: number;
  planned_minutes: number;
}
