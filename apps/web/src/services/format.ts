function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatPhase(phase: string): string {
  return `阶段 ${phase}`;
}

/** 更新时间只展示本地时区的时分秒，避免出现 Invalid Date。 */
export function formatTimestamp(timestamp: number | null): string {
  if (timestamp === null || !Number.isFinite(timestamp)) {
    return "—";
  }
  const date = new Date(timestamp);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
    date.getSeconds(),
  )}`;
}

/** 契约时间为 UTC 字符串；界面统一显示本地时区完整时间。 */
export function formatDateTime(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
    date.getSeconds(),
  )}`;
}

export function formatText(value: string | null | undefined): string {
  return typeof value === "string" && value.trim() !== "" ? value : "—";
}

/** 长 ID 中间省略，完整值由 Tooltip 或复制能力提供。 */
export function formatIdentifier(
  value: string | null | undefined,
  head = 8,
  tail = 4,
): string {
  if (typeof value !== "string" || value.trim() === "") {
    return "—";
  }
  const text = value.trim();
  if (text.length <= head + tail + 1) {
    return text;
  }
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

export function formatMetric(
  value: number | null | undefined,
  unit: string,
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return unit ? `${value} ${unit}` : String(value);
}

export function formatResolution(
  width: number | null,
  height: number | null,
): string {
  if (width === null || height === null) {
    return "—";
  }
  return `${width} × ${height}`;
}

/**
 * 能力描述的 Schema 尚未冻结，这里只列出顶层键，避免前端解释未知业务结构。
 */
export function formatCapabilityProfile(
  profile: Record<string, unknown> | null,
): string {
  if (!profile) {
    return "—";
  }
  const keys = Object.keys(profile);
  return keys.length === 0 ? "—" : keys.join("、");
}
