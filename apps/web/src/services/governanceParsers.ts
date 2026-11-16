/**
 * 经营分析响应解析（契约 `schemas.governance.yaml`）。
 *
 * 外部数据进入 UI 前必须校验：`used` / `limit` / `remaining` / `measured_at`
 * 允许多一种 `null`，这里不允许用 `?? 0` 之类把 `null` 洗成 0——那正是
 * 「未测量」被伪装成「用量为 0」的缺陷来源。
 */

import {
  QUOTA_STATUS_VALUES,
  RESOURCE_KIND_VALUES,
  type Quota,
  type QuotaList,
  type QuotaStatus,
  type ResourceKind,
  type UsageEntry,
  type UsageReport,
} from "../types/governance";
import {
  asRecord,
  ContractValidationError,
  readEnum,
  readInteger,
  readOptionalBoolean,
  readOptionalString,
  readOptionalUuid,
  readPositiveInteger,
  readString,
  readUuid,
} from "./validation";

/**
 * 计数型字段（字节数 / 条数）。
 *
 * 契约上限是 BIGINT（9.22e18），已超过 JS 安全整数范围；接受这种值会让
 * 后面所有格式化都静默丢精度，所以宁可判定为「响应不符合契约」。
 * 声明为负数同样不可能（`used` / `limit` / `remaining` 语义上非负）。
 */
function readQuantity(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number {
  const value = readInteger(record, key, path);
  if (!Number.isSafeInteger(value)) {
    throw new ContractValidationError(`${path} 超出可安全展示的整数范围`);
  }
  return value;
}

function readOptionalQuantity(
  record: Record<string, unknown>,
  key: string,
  path: string,
): number | null {
  if (record[key] === undefined || record[key] === null) {
    return null;
  }
  return readQuantity(record, key, path);
}

export function parseQuota(value: unknown, path = "quota"): Quota {
  const record = asRecord(value, path);
  return {
    quota_id: readUuid(record, "quota_id", path),
    project_id: readUuid(record, "project_id", path),
    resource_kind: readEnum(
      record,
      "resource_kind",
      path,
      RESOURCE_KIND_VALUES,
    ),
    limit: readQuantity(record, "limit", path),
    version: readPositiveInteger(record, "version", path),
    created_at: readOptionalString(record, "created_at", path),
    updated_at: readOptionalString(record, "updated_at", path),
    created_by: readOptionalUuid(record, "created_by", path),
    updated_by: readOptionalUuid(record, "updated_by", path),
  };
}

/**
 * 配额列表。
 *
 * 契约明确「只返回已配置的配额」：某类资源没有条目代表未设上限，
 * 这里不补空行，否则「没配置」会被伪装成「配置为空」。
 * 同类型出现多条属于服务端违约（每项目每类型唯一），直接拒绝。
 */
export function parseQuotaList(value: unknown): QuotaList {
  const record = asRecord(value, "配额列表");
  const rawItems = record.items;
  if (!Array.isArray(rawItems)) {
    throw new ContractValidationError("配额列表.items 必须是数组");
  }
  const items = rawItems.map((item, index) =>
    parseQuota(item, `配额列表.items[${index}]`),
  );
  const seen = new Set<ResourceKind>();
  for (const item of items) {
    if (seen.has(item.resource_kind)) {
      throw new ContractValidationError(
        `配额列表.items 中 ${item.resource_kind} 重复`,
      );
    }
    seen.add(item.resource_kind);
  }
  return { items };
}

export function parseUsageEntry(
  value: unknown,
  path = "usage_entry",
): UsageEntry {
  const record = asRecord(value, path);
  return {
    resource_kind: readEnum(
      record,
      "resource_kind",
      path,
      RESOURCE_KIND_VALUES,
    ),
    used: readOptionalQuantity(record, "used", path),
    limit: readOptionalQuantity(record, "limit", path),
    remaining: readOptionalQuantity(record, "remaining", path),
    exceeded: readOptionalBoolean(record, "exceeded", path),
    // 未知状态不得透传成「未知」或默认通过：契约枚举之外的值直接拒绝。
    status: readEnum<QuotaStatus>(
      record,
      "status",
      path,
      QUOTA_STATUS_VALUES,
    ),
    measurement_source: readOptionalString(record, "measurement_source", path),
    measured_at: readOptionalString(record, "measured_at", path),
  };
}

export function parseUsageReport(value: unknown): UsageReport {
  const record = asRecord(value, "用量报表");
  const rawEntries = record.entries;
  if (!Array.isArray(rawEntries)) {
    throw new ContractValidationError("用量报表.entries 必须是数组");
  }
  const entries = rawEntries.map((item, index) =>
    parseUsageEntry(item, `用量报表.entries[${index}]`),
  );
  const seen = new Set<ResourceKind>();
  for (const entry of entries) {
    if (seen.has(entry.resource_kind)) {
      throw new ContractValidationError(
        `用量报表.entries 中 ${entry.resource_kind} 重复`,
      );
    }
    seen.add(entry.resource_kind);
  }
  return {
    project_id: readUuid(record, "project_id", "用量报表"),
    // `generated_at` 是契约必填：缺失或非字符串都视为违约，不用本地时间兜底。
    generated_at: readString(record, "generated_at", "用量报表"),
    entries,
  };
}
