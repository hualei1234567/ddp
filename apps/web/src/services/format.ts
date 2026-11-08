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
