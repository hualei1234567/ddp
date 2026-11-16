/**
 * 经营分析（N9 规模化运营）类型：项目级资源配额与用量报表。
 *
 * 契约唯一来源：`packages/contracts/fragments/schemas.governance.yaml`。
 * 三态口径由契约固定，前端不得改写：
 * - `used = null` 表示**尚无可用统计**，不是 0；
 * - `limit = null` 表示**未配置上限**，不是上限为 0；
 * - `status = UNMEASURED` 与 `OK` 是两种不同结论，不得合并渲染。
 */

export const RESOURCE_KIND_VALUES = [
  "OBJECT_STORAGE_BYTES",
  "EPISODES",
  "DATASET_VERSIONS",
  "INGESTION_JOBS",
  "INFERENCE_TASKS",
] as const;

export type ResourceKind = (typeof RESOURCE_KIND_VALUES)[number];

export const QUOTA_STATUS_VALUES = [
  "UNMEASURED",
  "UNLIMITED",
  "OK",
  "AT_LIMIT",
  "OVER_LIMIT",
] as const;

export type QuotaStatus = (typeof QUOTA_STATUS_VALUES)[number];

/** 已配置的配额。`version` 是修改时必须回传的 `If-Match` 版本。 */
export interface Quota {
  quota_id: string;
  project_id: string;
  resource_kind: ResourceKind;
  /** 上限；`0` 是合法值（禁止任何新增），与「未配置」不同。 */
  limit: number;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface QuotaList {
  items: Quota[];
}

/** `PUT /quotas/{resource_kind}` 的请求体；`extra="forbid"`，只允许 limit。 */
export interface QuotaSetInput {
  limit: number;
}

/** 用量—配额对照的单条资源。 */
export interface UsageEntry {
  resource_kind: ResourceKind;
  /** 已用量；`null` 表示尚无可用统计，不是 0。 */
  used: number | null;
  /** 上限；`null` 表示未配置上限。 */
  limit: number | null;
  /** 剩余量；超限时被服务端截断为 0，`null` 表示无法计算。 */
  remaining: number | null;
  /** 是否已超限；`null` 表示无法判断。 */
  exceeded: boolean | null;
  status: QuotaStatus;
  /** 权威统计来源标识（表名或聚合口径）。 */
  measurement_source: string | null;
  /** 统计截止时间（UTC ISO）；`null` 表示该条目无测量时间。 */
  measured_at: string | null;
}

export interface UsageReport {
  project_id: string;
  /** 报表生成时间（UTC ISO）。 */
  generated_at: string;
  /** 服务端固定返回 5 条，顺序为 `ResourceKind` 声明顺序。 */
  entries: UsageEntry[];
}
