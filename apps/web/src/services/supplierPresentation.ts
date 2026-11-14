/** 阶段 6 供应商域枚举中文展示；只做展示，不推断权限。 */

import type { StatusPresentation } from "./datasetPresentation";
import type { SupplierMetrics } from "../types/supplier";

const SUPPLIER_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  ACTIVE: { label: "合作中", color: "green" },
  SUSPENDED: { label: "已暂停", color: "orange" },
  TERMINATED: { label: "已终止", color: "default" },
};

const CATEGORY: Record<string, string> = {
  DATA_COLLECTION: "数据采集",
  ANNOTATION: "数据标注",
  HARDWARE: "硬件设备",
  INTEGRATION: "集成实施",
  OTHER: "其他",
};

const BATCH_STATUS: Record<string, StatusPresentation> = {
  ANNOUNCED: { label: "已通知", color: "default" },
  IN_TRANSIT: { label: "运输中", color: "processing" },
  ARRIVED: { label: "已到货", color: "blue" },
  SCREENING: { label: "验收中", color: "processing" },
  ACCEPTED: { label: "全部通过", color: "green" },
  PARTIALLY_ACCEPTED: { label: "部分通过", color: "gold" },
  REJECTED: { label: "全部拒收", color: "red" },
  REWORK: { label: "返工中", color: "orange" },
  CLOSED: { label: "已关闭", color: "default" },
};

const SEGMENT_STATUS: Record<string, StatusPresentation> = {
  REGISTERED: { label: "已登记", color: "default" },
  PENDING_REVIEW: { label: "待审看", color: "processing" },
  ACCEPTED: { label: "已通过", color: "green" },
  REJECTED: { label: "已拒收", color: "red" },
  NEED_MORE: { label: "待补件", color: "orange" },
};

const DECISION: Record<string, StatusPresentation> = {
  ACCEPT: { label: "通过", color: "green" },
  REJECT: { label: "拒收", color: "red" },
  NEED_MORE: { label: "待补件", color: "orange" },
};

function lookup(table: Record<string, StatusPresentation>, value: string): StatusPresentation {
  return table[value] ?? { label: value, color: "default" };
}

export const presentSupplierStatus = (value: string) => lookup(SUPPLIER_STATUS, value);
export const presentBatchStatus = (value: string) => lookup(BATCH_STATUS, value);
export const presentSegmentStatus = (value: string) => lookup(SEGMENT_STATUS, value);
export const presentDecision = (value: string) => lookup(DECISION, value);

export function presentCategory(value: string): string {
  return CATEGORY[value] ?? value;
}

const ACTION_LABEL: Record<string, string> = {
  activate: "激活",
  suspend: "暂停合作",
  resume: "恢复合作",
  terminate: "终止合作",
  dispatch: "通知发货",
  arrive: "确认到货",
  "start-screening": "开始验收",
  rework: "要求返工",
  close: "关闭批次",
  "queue-review": "进入审看",
  resubmit: "补件重提",
};

export function presentSupplierAction(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

/** 拒收与待补件必须写明原因；终止与返工也要原因。 */
export function requiresReason(action: string): boolean {
  return ["terminate", "rework", "reject", "dismiss"].includes(action);
}

/** 指标缺失显示「暂无」，绝不显示 0。 */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "暂无";
  }
  return `${(value * 100).toFixed(1)}%`;
}

export function formatHours(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "暂无";
  }
  return `${value.toFixed(1)} 小时`;
}

/** 诊断快照只做只读展示；不解释、不推断业务结论。 */
export function diagnosticEntries(metrics: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(metrics)
    .filter(([key]) => !key.startsWith("_"))
    .map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)]);
}

export function metricsSummary(metrics: SupplierMetrics): Array<{ label: string; value: string }> {
  return [
    { label: "到货批次", value: String(metrics.batch_count) },
    { label: "已终审", value: String(metrics.settled_batch_count) },
    { label: "及时率", value: formatRate(metrics.on_time_rate) },
    { label: "一次通过率", value: formatRate(metrics.first_pass_rate) },
    { label: "返工率", value: formatRate(metrics.rework_rate) },
    { label: "平均验收周期", value: formatHours(metrics.average_cycle_hours) },
    {
      label: "片段通过率",
      value: `${formatRate(metrics.segment_pass_rate)}（${metrics.accepted_segments}/${metrics.decided_segments}）`,
    },
  ];
}
