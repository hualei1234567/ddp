import type { CalibrationRecord, Device } from "../types/device";

/**
 * 设备新鲜度只根据服务端返回的元数据判断：
 * - `freshness` 明确给出 stale/fresh 时以服务端为准；
 * - 提供 `last_heartbeat_at` 与 `heartbeat_timeout_seconds` 时才比较，
 *   前端不提供默认阈值，避免硬编码业务规则；
 * - 两者都缺失时返回 unknown，页面不得把未知状态当成“可用”。
 */
export type FreshnessLevel = "fresh" | "stale" | "unknown";

export type FreshnessReason = "server" | "heartbeat" | "unknown";

export interface DeviceFreshness {
  level: FreshnessLevel;
  reason: FreshnessReason;
  lastHeartbeatAt: string | null;
  lastSyncedAt: string | null;
  /** 服务端下发的超时阈值（秒）；未下发时为 null。 */
  timeoutSeconds: number | null;
}

export interface DeviceCollectionFreshness {
  staleCount: number;
  unknownCount: number;
  /** 超时设备中最近的一次心跳，用于列表 Stale 提示。 */
  latestHeartbeatAt: string | null;
  /** 仅用于测试与逐行标记，不作为渲染顺序依据。 */
  staleDeviceIds: string[];
}

export interface CalibrationExpirySummary {
  expiredCount: number;
  /** 已过期记录中最晚的 `valid_to`，用于说明影响范围。 */
  latestExpiredValidTo: string | null;
}

const STALE_SERVER_VALUES = new Set([
  "stale",
  "expired",
  "outdated",
  "out_of_date",
  "outdated_data",
]);

const FRESH_SERVER_VALUES = new Set([
  "fresh",
  "ok",
  "current",
  "realtime",
  "real_time",
]);

function parseTime(value: string | null | undefined): number | null {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

function isPositiveNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function evaluateDeviceFreshness(
  device: Device,
  now: number = Date.now(),
): DeviceFreshness {
  const lastHeartbeatAt = device.last_heartbeat_at ?? null;
  const lastSyncedAt = device.last_synced_at ?? null;
  const timeoutSeconds = isPositiveNumber(device.heartbeat_timeout_seconds)
    ? device.heartbeat_timeout_seconds
    : null;

  const declared = (device.freshness ?? "").trim().toLowerCase();
  if (STALE_SERVER_VALUES.has(declared)) {
    return {
      level: "stale",
      reason: "server",
      lastHeartbeatAt,
      lastSyncedAt,
      timeoutSeconds,
    };
  }
  if (FRESH_SERVER_VALUES.has(declared)) {
    return {
      level: "fresh",
      reason: "server",
      lastHeartbeatAt,
      lastSyncedAt,
      timeoutSeconds,
    };
  }

  const heartbeatAt = parseTime(lastHeartbeatAt);
  if (heartbeatAt !== null && timeoutSeconds !== null) {
    const ageMs = now - heartbeatAt;
    return {
      level: ageMs > timeoutSeconds * 1000 ? "stale" : "fresh",
      reason: "heartbeat",
      lastHeartbeatAt,
      lastSyncedAt,
      timeoutSeconds,
    };
  }

  // 没有服务端阈值或心跳时间时无法判断，不得推测为可用或过期。
  return {
    level: "unknown",
    reason: "unknown",
    lastHeartbeatAt,
    lastSyncedAt,
    timeoutSeconds,
  };
}

export function summarizeDeviceFreshness(
  devices: Device[],
  now: number = Date.now(),
): DeviceCollectionFreshness {
  let staleCount = 0;
  let unknownCount = 0;
  let latestHeartbeat = Number.NEGATIVE_INFINITY;
  let latestHeartbeatAt: string | null = null;
  const staleDeviceIds: string[] = [];

  for (const device of devices) {
    const freshness = evaluateDeviceFreshness(device, now);
    if (freshness.level === "unknown") {
      unknownCount += 1;
      continue;
    }
    if (freshness.level !== "stale") {
      continue;
    }

    staleCount += 1;
    staleDeviceIds.push(device.device_id);
    const heartbeatAt = parseTime(freshness.lastHeartbeatAt);
    if (heartbeatAt !== null && heartbeatAt > latestHeartbeat) {
      latestHeartbeat = heartbeatAt;
      latestHeartbeatAt = freshness.lastHeartbeatAt;
    }
  }

  return { staleCount, unknownCount, latestHeartbeatAt, staleDeviceIds };
}

/** 参与过期告警的状态：当前生效或服务端已判定过期。 */
const EXPIRY_WARNING_STATUSES = new Set(["ACTIVE", "EXPIRED"]);

/**
 * 标定是否过期：状态为 EXPIRED，或 `valid_to` 已经早于当前时间。
 * 这里比较的是记录自身的有效期，不是前端硬编码的标定阈值。
 */
export function isCalibrationExpired(
  record: CalibrationRecord,
  now: number = Date.now(),
): boolean {
  if (record.status.trim().toUpperCase() === "EXPIRED") {
    return true;
  }
  const validTo = parseTime(record.valid_to);
  return validTo !== null && validTo <= now;
}

export function summarizeCalibrationExpiry(
  records: CalibrationRecord[],
  now: number = Date.now(),
): CalibrationExpirySummary {
  let expiredCount = 0;
  let latestExpired = Number.NEGATIVE_INFINITY;
  let latestExpiredValidTo: string | null = null;

  for (const record of records) {
    // 只有当前生效或服务端已标记过期的记录才需要提示；
    // SUPERSEDED/REVOKED/草稿等历史记录不参与过期告警。
    if (!EXPIRY_WARNING_STATUSES.has(record.status.trim().toUpperCase())) {
      continue;
    }
    if (!isCalibrationExpired(record, now)) {
      continue;
    }
    expiredCount += 1;
    const validTo = parseTime(record.valid_to);
    if (validTo !== null && validTo > latestExpired) {
      latestExpired = validTo;
      latestExpiredValidTo = record.valid_to;
    }
  }

  return { expiredCount, latestExpiredValidTo };
}
