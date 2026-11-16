/**
 * 经营分析展示层（纯函数，可单测）。
 *
 * 这里只解决「服务端值怎么显示」，不做权限判断、不推断状态机。
 *
 * 三条不可合并的口径（契约 `schemas.governance.yaml` 原文）：
 * 1. `used = null` → **未测量**，不是 0；真实测到 0 才显示 0；
 * 2. `limit = null` → **未配置上限**，不是上限为 0；
 * 3. `status = UNMEASURED` 与 `OK` 是两种结论，`UNMEASURED` 不得用绿色/「正常」。
 */

import type { ApiError } from "./api";
import type {
  Quota,
  QuotaStatus,
  ResourceKind,
  UsageEntry,
} from "../types/governance";

/** 与 `components/ui/StatusTag` 的展示契约一致（label + color）。 */
export interface StatusPresentation {
  label: string;
  color: string;
}

const RESOURCE_KIND_LABELS: Record<ResourceKind, string> = {
  OBJECT_STORAGE_BYTES: "对象存储占用",
  EPISODES: "Episode",
  DATASET_VERSIONS: "数据集版本",
  INGESTION_JOBS: "接入任务",
  INFERENCE_TASKS: "推理任务",
};

/** 未知类型原样展示，不猜语义（前后端版本可能不一致）。 */
export function resourceKindLabel(kind: string): string {
  return RESOURCE_KIND_LABELS[kind as ResourceKind] ?? kind;
}

/** `OBJECT_STORAGE_BYTES` 是字节数，其余四项是记录条数。 */
export function isByteResource(kind: ResourceKind): boolean {
  return kind === "OBJECT_STORAGE_BYTES";
}

const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB"] as const;

/**
 * 字节的人类可读格式（二进制单位，与契约字段名 bytes 对应）。
 *
 * 负数或非有限值来自上游违约，显示为 `—` 而不是 `0 B`。
 */
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return "—";
  }
  let scaled = value;
  let unitIndex = 0;
  while (scaled >= 1024 && unitIndex < BYTE_UNITS.length - 1) {
    scaled /= 1024;
    unitIndex += 1;
  }
  const decimals = unitIndex === 0 ? 0 : 1;
  return `${scaled.toFixed(decimals)} ${BYTE_UNITS[unitIndex]}`;
}

/** 条数：千分位分组。不依赖 `toLocaleString`，保证测试与各端输出一致。 */
export function formatCount(value: number): string {
  if (!Number.isFinite(value) || value < 0) {
    return "—";
  }
  return String(Math.trunc(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 按资源类型格式化用量：字节型走 KiB/MiB，其余按条数。 */
export function formatUsageQuantity(kind: ResourceKind, value: number): string {
  return isByteResource(kind) ? formatBytes(value) : formatCount(value);
}

/** 未被测量时的统一文案与解释（页面、Tooltip 复用同一份）。 */
export const UNMEASURED_USED_LABEL = "未测量";
export const UNMEASURED_USED_HINT =
  "尚无可用统计快照，不代表用量为 0。";
export const UNLIMITED_LIMIT_LABEL = "未配置上限";
export const UNCOMPUTABLE_REMAINING_LABEL = "无法计算";

/** 已用量：`null` 必须显示「未测量」，绝不可回退成 0。 */
export function presentUsed(entry: UsageEntry): string {
  return entry.used === null
    ? UNMEASURED_USED_LABEL
    : formatUsageQuantity(entry.resource_kind, entry.used);
}

/** 上限：`null` 显示「未配置上限」；0 是合法上限，照常显示 0。 */
export function presentLimit(entry: UsageEntry): string {
  return entry.limit === null
    ? UNLIMITED_LIMIT_LABEL
    : formatUsageQuantity(entry.resource_kind, entry.limit);
}

/** 剩余量：超限时服务端截断为 0，这里只展示服务端的值，不自己重算。 */
export function presentRemaining(entry: UsageEntry): string {
  return entry.remaining === null
    ? UNCOMPUTABLE_REMAINING_LABEL
    : formatUsageQuantity(entry.resource_kind, entry.remaining);
}

/**
 * 状态标签。
 *
 * `UNMEASURED` 用中性灰，刻意不用绿色/「正常」——它表示「没有结论」，
 * 与 `OK`（已测量且在限内）是两种结论。
 */
export function presentQuotaStatus(value: string): StatusPresentation {
  switch (value) {
    case "OK":
      return { label: "正常", color: "green" };
    case "AT_LIMIT":
      return { label: "已达上限", color: "orange" };
    case "OVER_LIMIT":
      return { label: "已超限", color: "red" };
    case "UNLIMITED":
      return { label: "未设上限", color: "blue" };
    case "UNMEASURED":
      return { label: "未测量", color: "default" };
    default:
      return { label: value, color: "default" };
  }
}

/** 超限标记：`exceeded = null` 表示无法判断，不当成 false。 */
export function presentExceeded(entry: UsageEntry): string {
  if (entry.exceeded === true) {
    return "已超限";
  }
  if (entry.exceeded === false) {
    return "未超限";
  }
  return "无法判断";
}

/**
 * 报表行：用量条目 + 该类型已配置配额的版本号。
 *
 * 用量报表本身不带 `version`，修改上限必须用 `GET /quotas` 的版本号做
 * `If-Match`，因此两个数据源在展示层合并，行数仍以用量报表为准。
 */
export interface UsageRow {
  entry: UsageEntry;
  /** 已配置配额的版本；`null` 表示该类型尚未配置上限（新建不需要 If-Match）。 */
  quotaVersion: number | null;
}

export function mergeQuotaVersions(
  entries: readonly UsageEntry[],
  quotas: readonly Quota[],
): UsageRow[] {
  const versionByKind = new Map<ResourceKind, number>();
  for (const quota of quotas) {
    versionByKind.set(quota.resource_kind, quota.version);
  }
  return entries.map((entry) => ({
    entry,
    quotaVersion: versionByKind.get(entry.resource_kind) ?? null,
  }));
}

/** 状态筛选在客户端进行：契约没有为报表声明过滤参数。 */
export function filterUsageRows(
  rows: readonly UsageRow[],
  status: QuotaStatus | undefined,
): UsageRow[] {
  if (status === undefined) {
    return [...rows];
  }
  return rows.filter((row) => row.entry.status === status);
}

/** 配额修改失败的反馈；`refreshRequired` 表示本地版本已失效。 */
export interface QuotaWriteFailure {
  message: string;
  refreshRequired: boolean;
}

/**
 * 写失败的用户反馈。
 *
 * 403 如实说明「服务端拒绝」，不把按钮藏起来假装没权限；
 * 409 / 428 都意味着本地版本过期，提示刷新后重试。
 */
export function describeQuotaWriteError(error: ApiError): QuotaWriteFailure {
  if (error.isForbidden) {
    return {
      message: "服务端拒绝：当前账号没有修改项目配额的权限（403）。",
      refreshRequired: false,
    };
  }
  if (error.isConflict) {
    return {
      message: "版本冲突（409）：配额已被其他管理员修改，请刷新后按最新版本重试。",
      refreshRequired: true,
    };
  }
  if (error.status === 428) {
    return {
      message: "服务端要求 If-Match（428）：该类型已有配额，必须带版本号修改。请刷新后重试。",
      refreshRequired: true,
    };
  }
  return { message: error.message, refreshRequired: false };
}

/** 编辑弹窗里的输入校验；`0` 合法（禁止任何新增），空值不合法。 */
export function validateQuotaLimit(
  value: number | null | undefined,
): string | null {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "请输入上限（0 表示禁止任何新增）";
  }
  if (!Number.isInteger(value) || value < 0) {
    return "上限必须是非负整数";
  }
  if (!Number.isSafeInteger(value)) {
    return "上限超出可安全提交的整数范围";
  }
  return null;
}
