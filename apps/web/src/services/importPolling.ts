/** 导入进度自动刷新口径：只在 RUNNING 时轮询，终态立即停止。 */

import type { ImportStatus } from "../types/datasource";

/** 5–10 秒区间取中值；太快会压服务端，太慢会让人以为页面卡住。 */
export const IMPORT_POLL_INTERVAL_MS = 7_000;

/**
 * 是否还有需要自动刷新的导入。
 *
 * 只有 `RUNNING` 会继续变化；`PENDING` 尚未开始执行，终态不会再变。
 * 空列表返回 false，避免页面停留时无意义地空转。
 */
export function hasRunningImport(
  items: readonly { status: ImportStatus }[],
): boolean {
  return items.some((item) => item.status === "RUNNING");
}
