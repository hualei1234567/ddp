import { describe, expect, it } from "vitest";
import { sortImportLogsNewestFirst, type ImportLogOrderKey } from "./importLogs";

function log(logId: string, occurredAt: string): ImportLogOrderKey {
  return { log_id: logId, occurred_at: occurredAt };
}

describe("导入日志展示顺序", () => {
  it("跨页累积后仍按时间倒序，不把更早页内部顺序翻反", () => {
    // 第一页（服务端正序）：09:02, 09:03；第二页追加：09:00, 09:01。
    const rows = sortImportLogsNewestFirst([
      log("b", "2026-11-18T09:02:00Z"),
      log("c", "2026-11-18T09:03:00Z"),
      log("a", "2026-11-18T09:00:00Z"),
      log("b2", "2026-11-18T09:01:00Z"),
    ]);

    expect(rows.map((item) => item.log_id)).toEqual(["c", "b", "b2", "a"]);
  });

  it("同一时刻按 log_id 倒序，保证稳定", () => {
    const at = "2026-11-18T09:00:00Z";
    const rows = sortImportLogsNewestFirst([log("aaa", at), log("ccc", at), log("bbb", at)]);

    expect(rows.map((item) => item.log_id)).toEqual(["ccc", "bbb", "aaa"]);
  });

  it("不修改入参", () => {
    const source = [log("a", "2026-11-18T09:00:00Z"), log("b", "2026-11-18T09:01:00Z")];

    sortImportLogsNewestFirst(source);

    expect(source.map((item) => item.log_id)).toEqual(["a", "b"]);
  });
});

  it("时间戳无法解析时仍按 log_id 倒序，不产生未定义顺序", () => {
    const rows = sortImportLogsNewestFirst([log("aaa", "not-a-time"), log("ccc", "not-a-time")]);

    expect(rows.map((item) => item.log_id)).toEqual(["ccc", "aaa"]);
  });
