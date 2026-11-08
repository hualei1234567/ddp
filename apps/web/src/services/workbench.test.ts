import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  createWorkbenchSnapshot,
  hasForbiddenFailure,
  hasPartialFailure,
  isFullyForbidden,
} from "./workbench";

const health = {
  status: "ok",
  service: "DDP",
  version: "0.1.0",
  env: "local",
};

const meta = {
  name: "DDP",
  version: "0.1.0",
  env: "local",
  phase: "0",
  modules: ["platform"],
};

describe("工作台数据聚合", () => {
  it("两个接口成功时没有部分失败", () => {
    const snapshot = createWorkbenchSnapshot(
      { status: "fulfilled", value: health },
      { status: "fulfilled", value: meta },
    );

    expect(hasPartialFailure(snapshot)).toBe(false);
    expect(hasForbiddenFailure(snapshot)).toBe(false);
    expect(snapshot.health.data).toEqual(health);
    expect(snapshot.meta.data).toEqual(meta);
  });

  it("单个接口失败时保留另一个接口的数据", () => {
    const snapshot = createWorkbenchSnapshot(
      {
        status: "rejected",
        reason: new ApiError({ kind: "network", message: "断网" }),
      },
      { status: "fulfilled", value: meta },
    );

    expect(hasPartialFailure(snapshot)).toBe(true);
    expect(snapshot.health.data).toBeNull();
    expect(snapshot.health.error?.kind).toBe("network");
    expect(snapshot.meta.data).toEqual(meta);
  });

  it("非 ApiError 的失败统一归一化", () => {
    const snapshot = createWorkbenchSnapshot(
      { status: "rejected", reason: new Error("boom") },
      { status: "fulfilled", value: meta },
    );

    expect(snapshot.health.error).toBeInstanceOf(ApiError);
    expect(snapshot.health.error?.kind).toBe("network");
  });

  it("识别 403 与全部 403 场景", () => {
    const forbidden = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问",
    });
    const snapshot = createWorkbenchSnapshot(
      { status: "rejected", reason: forbidden },
      { status: "rejected", reason: forbidden },
    );

    expect(hasPartialFailure(snapshot)).toBe(true);
    expect(hasForbiddenFailure(snapshot)).toBe(true);
    expect(isFullyForbidden(snapshot)).toBe(true);
  });

  it("仅部分接口 403 时不算全部禁止", () => {
    const snapshot = createWorkbenchSnapshot(
      {
        status: "rejected",
        reason: new ApiError({ kind: "http", status: 403, message: "无权访问" }),
      },
      { status: "fulfilled", value: meta },
    );

    expect(hasForbiddenFailure(snapshot)).toBe(true);
    expect(isFullyForbidden(snapshot)).toBe(false);
  });
});
