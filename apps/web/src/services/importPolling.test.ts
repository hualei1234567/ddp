import { describe, expect, it } from "vitest";

import type { ImportStatus } from "../types/datasource";
import { IMPORT_POLL_INTERVAL_MS, hasRunningImport } from "./importPolling";

function items(statuses: ImportStatus[]): { status: ImportStatus }[] {
  return statuses.map((status) => ({ status }));
}

describe("导入轮询口径", () => {
  it("轮询间隔落在 5–10 秒区间", () => {
    expect(IMPORT_POLL_INTERVAL_MS).toBeGreaterThanOrEqual(5_000);
    expect(IMPORT_POLL_INTERVAL_MS).toBeLessThanOrEqual(10_000);
  });

  it("只要存在 RUNNING 任务就继续轮询", () => {
    expect(hasRunningImport(items(["RUNNING"]))).toBe(true);
    expect(hasRunningImport(items(["SUCCEEDED", "RUNNING"]))).toBe(true);
  });

  it("PENDING 与所有终态都不触发轮询", () => {
    expect(hasRunningImport(items(["PENDING"]))).toBe(false);
    expect(
      hasRunningImport(items(["SUCCEEDED", "FAILED", "CANCELLED"])),
    ).toBe(false);
    expect(hasRunningImport([])).toBe(false);
  });
});
