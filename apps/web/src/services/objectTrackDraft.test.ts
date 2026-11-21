import { describe, expect, it } from "vitest";

import {
  objectTrackRowIssue,
  parseObjectTrackDraft,
  serializeObjectTrackDraft,
  type ObjectTrackRow,
} from "./objectTrackDraft";

describe("parseObjectTrackDraft", () => {
  it("把合法数组解析成可编辑行，保留额外字段", () => {
    const result = parseObjectTrackDraft(
      '[{"start_time_ns":0,"end_time_ns":1000,"track_id":"t1","bbox":[0,0,10,10]}]',
    );

    expect(result.error).toBeNull();
    expect(result.rows).toEqual([
      {
        startNs: "0",
        endNs: "1000",
        extra: { track_id: "t1", bbox: [0, 0, 10, 10] },
      },
    ]);
  });

  it("空文本解析为空行而不是错误", () => {
    expect(parseObjectTrackDraft("   ")).toEqual({ rows: [], error: null });
  });

  it("非 JSON 返回错误且不丢原文", () => {
    const result = parseObjectTrackDraft("not json");

    expect(result.rows).toEqual([]);
    expect(result.error).toMatch(/JSON/);
  });

  it("非数组或空数组视为不可结构化", () => {
    expect(parseObjectTrackDraft("{}").error).toMatch(/数组/);
    expect(parseObjectTrackDraft("[]").error).toMatch(/数组/);
  });

  it("缺少整数 start/end 的行不会被静默修正", () => {
    expect(
      parseObjectTrackDraft('[{"start_time_ns":"0","end_time_ns":1000}]').error,
    ).toMatch(/start_time_ns/);
  });
});

describe("serializeObjectTrackDraft", () => {
  it("把行序列化成后端接受的数组，额外字段排在前", () => {
    const rows: ObjectTrackRow[] = [
      { startNs: "0", endNs: "1000", extra: { track_id: "t1" } },
    ];

    expect(JSON.parse(serializeObjectTrackDraft(rows))).toEqual([
      { track_id: "t1", start_time_ns: 0, end_time_ns: 1000 },
    ]);
  });
});

describe("objectTrackRowIssue", () => {
  it("合法行无问题", () => {
    expect(
      objectTrackRowIssue({ startNs: "0", endNs: "1000", extra: {} }),
    ).toBeNull();
  });

  it("开始不小于结束时给出原因", () => {
    expect(
      objectTrackRowIssue({ startNs: "1000", endNs: "1000", extra: {} }),
    ).toMatch(/小于/);
  });

  it("非整数纳秒被拒绝", () => {
    expect(
      objectTrackRowIssue({ startNs: "1.5", endNs: "1000", extra: {} }),
    ).toMatch(/整数/);
    expect(
      objectTrackRowIssue({ startNs: "", endNs: "1000", extra: {} }),
    ).toMatch(/整数/);
  });

  it("尊重 Schema 的最小/最大时长", () => {
    const spec = { min_duration_ns: 100 };
    expect(
      objectTrackRowIssue({ startNs: "0", endNs: "50", extra: {} }, spec),
    ).toMatch(/最小允许时长/);
  });
});
