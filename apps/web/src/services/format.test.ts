import { describe, expect, it } from "vitest";

import {
  formatCapabilityProfile,
  formatDateTime,
  formatIdentifier,
  formatMetric,
  formatPhase,
  formatResolution,
  formatText,
  formatTimestamp,
} from "./format";

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

describe("设备域格式化", () => {
  it("绝对时间按本地时区输出完整时间，非法值使用占位符", () => {
    expect(formatDateTime("2026-11-08T12:34:56")).toBe(
      "2026-11-08 12:34:56",
    );
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("not-a-date")).toBe("—");
  });

  it("长 ID 中间省略，短值与缺失值保持可读", () => {
    expect(
      formatIdentifier("11111111-2222-7333-8444-555555555555"),
    ).toBe("11111111…5555");
    expect(formatIdentifier("short")).toBe("short");
    expect(formatIdentifier(null)).toBe("—");
  });

  it("数值、分辨率与能力描述不把缺失值显示为 0", () => {
    expect(formatMetric(30, "Hz")).toBe("30 Hz");
    expect(formatMetric(null, "Hz")).toBe("—");
    expect(formatResolution(1920, 1080)).toBe("1920 × 1080");
    expect(formatResolution(null, 1080)).toBe("—");
    expect(formatCapabilityProfile({ dof: 7, interfaces: [] })).toBe(
      "dof、interfaces",
    );
    expect(formatCapabilityProfile(null)).toBe("—");
  });

  it("文本缺失统一使用占位符", () => {
    expect(formatText("Acme")).toBe("Acme");
    expect(formatText("   ")).toBe("—");
    expect(formatText(null)).toBe("—");
  });
});
