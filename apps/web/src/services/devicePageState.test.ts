import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { resolveDevicePagePhase } from "./devicePageState";

function httpError(status: number): ApiError {
  return new ApiError({
    kind: "http",
    status,
    message: `HTTP ${status}`,
  });
}

describe("设备页面状态优先级", () => {
  it("403 优先于缓存数据，避免权限被拒后继续渲染旧数据", () => {
    expect(
      resolveDevicePagePhase({
        hasData: true,
        loading: false,
        error: httpError(403),
      }),
    ).toBe("forbidden");
  });

  it("404 优先于 Loading，避免显示不属于当前项目的资源", () => {
    expect(
      resolveDevicePagePhase({
        hasData: false,
        loading: true,
        error: httpError(404),
      }),
    ).toBe("not-found");
  });

  it("首次加载且无数据时为 Loading", () => {
    expect(
      resolveDevicePagePhase({
        hasData: false,
        loading: true,
        error: null,
      }),
    ).toBe("loading");
  });

  it("首次请求失败时为 Error", () => {
    expect(
      resolveDevicePagePhase({
        hasData: false,
        loading: false,
        error: httpError(500),
      }),
    ).toBe("error");
  });

  it("刷新失败但保留旧数据时继续渲染内容并由页面提示 Stale", () => {
    expect(
      resolveDevicePagePhase({
        hasData: true,
        loading: false,
        error: httpError(503),
      }),
    ).toBe("ready");
  });

  it("加载成功时为 Ready", () => {
    expect(
      resolveDevicePagePhase({
        hasData: true,
        loading: false,
        error: null,
      }),
    ).toBe("ready");
  });
});
