import { describe, expect, it } from "vitest";

import {
  presentAnnotationAction,
  requiresAnnotationReason,
  resolveAnnotationActions,
} from "./annotationActions";

describe("标注动作解析（后端未下发 allowed_actions 时的兜底）", () => {
  it("服务端下发动作时优先使用，并剔除系统动作", () => {
    const resolution = resolveAnnotationActions("task", "DRAFT", [
      "approve",
      "system-review",
      "cancel",
    ]);
    expect(resolution.source).toBe("server");
    expect(resolution.actions).toEqual(["approve", "cancel"]);
  });

  it("服务端明确下发空数组时不回退到状态推导", () => {
    const resolution = resolveAnnotationActions("batch", "PLANNED", []);
    expect(resolution.source).toBe("server");
    expect(resolution.actions).toEqual([]);
  });

  it("无服务端字段时按状态迁移表推导任务动作", () => {
    expect(resolveAnnotationActions("task", "REVIEWING").actions).toEqual([
      "accept",
      "reject",
      "escalate",
      "cancel",
    ]);
    expect(resolveAnnotationActions("task", "COMPLETED").actions).toEqual([
      "archive",
    ]);
  });

  it("系统动作不会出现在兜底按钮中", () => {
    expect(resolveAnnotationActions("task", "SUBMITTED").actions).toEqual([
      "cancel",
    ]);
    expect(resolveAnnotationActions("assignment", "ASSIGNED").actions).toEqual([
      "claim",
      "revoke",
    ]);
  });

  it("未知或空状态不猜动作", () => {
    expect(resolveAnnotationActions("schema", "UNKNOWN").actions).toEqual([]);
    expect(resolveAnnotationActions("schema", null).actions).toEqual([]);
  });

  it("原因必填与会后端状态机一致", () => {
    expect(requiresAnnotationReason("task", "pause")).toBe(true);
    expect(requiresAnnotationReason("task", "complete")).toBe(false);
    expect(requiresAnnotationReason("sample-set", "cancel")).toBe(true);
    expect(requiresAnnotationReason("schema", "publish")).toBe(false);
    expect(requiresAnnotationReason("assignment", "release")).toBe(true);
    expect(requiresAnnotationReason("quality-sample", "fail")).toBe(true);
  });

  it("未知动作原样展示，不猜语义", () => {
    expect(presentAnnotationAction("publish")).toBe("发布");
    expect(presentAnnotationAction("unknown-action")).toBe("unknown-action");
  });
});
