import { describe, expect, it } from "vitest";

import { formatPhase, formatTimestamp } from "./format";

describe("formatPhase", () => {
  it("统一阶段文案", () => {
    expect(formatPhase("0")).toBe("阶段 0");
  });
});

describe("formatTimestamp", () => {
  it("无数据或非法时间使用占位符", () => {
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp(Number.NaN)).toBe("—");
    expect(formatTimestamp(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("合法时间输出时分秒", () => {
    expect(formatTimestamp(Date.now())).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
