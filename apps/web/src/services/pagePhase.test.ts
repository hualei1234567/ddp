import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { resolvePagePhase } from "./pagePhase";

const forbidden = new ApiError({ kind: "http", status: 403, message: "无权" });
const notFound = new ApiError({ kind: "http", status: 404, message: "不存在" });
const serverError = new ApiError({ kind: "http", status: 500, message: "失败" });

describe("详情页主状态", () => {
  it("Forbidden/Not Found 优先于缓存数据与加载", () => {
    expect(
      resolvePagePhase({ hasData: true, loading: false, error: forbidden }),
    ).toBe("forbidden");
    expect(
      resolvePagePhase({ hasData: true, loading: false, error: notFound }),
    ).toBe("not-found");
    expect(
      resolvePagePhase({ hasData: false, loading: true, error: forbidden }),
    ).toBe("forbidden");
  });

  it("首屏加载、首次失败与有数据刷新失败", () => {
    expect(
      resolvePagePhase({ hasData: false, loading: true, error: null }),
    ).toBe("loading");
    expect(
      resolvePagePhase({ hasData: false, loading: false, error: serverError }),
    ).toBe("error");
    expect(
      resolvePagePhase({ hasData: true, loading: false, error: serverError }),
    ).toBe("ready");
  });
});
