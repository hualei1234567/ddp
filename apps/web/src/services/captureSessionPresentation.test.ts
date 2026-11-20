import { describe, expect, it } from "vitest";

import { presentCaptureSessionStatus, resolveCaptureSessionActions, captureSessionArtifactPath } from "./captureSessionPresentation";

describe("采集会话展示", () => {
  it("只解析五个受支持动作且保持服务端顺序", () => {
    expect(resolveCaptureSessionActions(["resume", "delete", "complete"])).toEqual([
      { action: "resume", label: "恢复采集", requiresReason: true, danger: false },
      { action: "complete", label: "完成采集", requiresReason: false, danger: false },
    ]);
  });

  it("展示状态并使用现有真实产物详情路径", () => {
    expect(presentCaptureSessionStatus("PAUSED").label).toBe("已暂停");
    expect(captureSessionArtifactPath("RAW_ASSET", "a/b")).toBe("/raw-assets/a%2Fb");
    expect(captureSessionArtifactPath("EPISODE", "e/1")).toBe("/episodes/e%2F1");
  });
});
