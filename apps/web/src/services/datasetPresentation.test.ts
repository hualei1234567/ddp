import { describe, expect, it } from "vitest";

import { presentExecutionMode, presentExternalStatus } from "./datasetPresentation";

describe("presentExternalStatus", () => {
  it("把演练终态与真实成功区分开", () => {
    expect(presentExternalStatus("SUCCEEDED")).toEqual({ label: "成功", color: "green" });
    const dryRun = presentExternalStatus("DRY_RUN");
    expect(dryRun.label).toContain("演练");
    expect(dryRun.color).not.toBe("green");
  });

  it("未知状态原样展示且不冒充成功", () => {
    const unknown = presentExternalStatus("SOMETHING_NEW");
    expect(unknown).toEqual({ label: "SOMETHING_NEW", color: "default" });
  });
});

describe("presentExecutionMode", () => {
  it("演练与模拟模式用高对比色标注", () => {
    expect(presentExecutionMode("MOCK").label).toContain("MOCK");
    expect(presentExecutionMode("DRY_RUN").label).toContain("DRY_RUN");
    expect(presentExecutionMode("DRY_RUN").color).toBe("orange");
  });
});
