import { describe, expect, it } from "vitest";

import { ApiError } from "../services/api";
import { requestFailureState, requestSuccessState } from "./useRequest";

const error = new ApiError({
  kind: "http",
  status: 503,
  message: "服务暂时不可用",
});

describe("请求状态机", () => {
  it("刷新失败时保留旧数据并标记 Stale", () => {
    const previous = requestSuccessState({ name: "旧设备档案" });
    const next = requestFailureState(previous, error);

    expect(next.data).toEqual({ name: "旧设备档案" });
    expect(next.error).toBe(error);
    expect(next.stale).toBe(true);
    expect(next.loading).toBe(false);
    expect(next.refreshing).toBe(false);
  });

  it("首次失败没有旧数据时不标记 Stale", () => {
    const next = requestFailureState(
      {
        data: null,
        error: null,
        loading: true,
        refreshing: false,
        stale: false,
        updatedAt: null,
      },
      error,
    );

    expect(next.data).toBeNull();
    expect(next.stale).toBe(false);
    expect(next.loading).toBe(false);
  });

  it("成功响应清除错误与 Stale", () => {
    const next = requestSuccessState([1, 2, 3]);

    expect(next).toMatchObject({
      data: [1, 2, 3],
      error: null,
      stale: false,
      loading: false,
      refreshing: false,
    });
    expect(next.updatedAt).not.toBeNull();
  });
});
