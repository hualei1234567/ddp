/** 导入进度自动刷新口径：只在 RUNNING 时轮询，终态立即停止。 */

import type { ImportStatus } from "../types/datasource";

/** 5–10 秒区间取中值；太快会压服务端，太慢会让人以为页面卡住。 */
export const IMPORT_POLL_INTERVAL_MS = 7_000;

/**
 * 是否还有需要自动刷新的导入。
 *
 * `PENDING` 也在队列里：Worker 领取后会自动推向 RUNNING，若只认 RUNNING，
 * 新建导入的页面会停在 PENDING 直到用户手动刷新。终态（SUCCEEDED / FAILED /
 * CANCELLED）不再变化。空列表返回 false，避免无意义空转。
 */
export function hasRunningImport(
  items: readonly { status: ImportStatus }[],
): boolean {
  return items.some((item) => item.status === "PENDING" || item.status === "RUNNING");
}
