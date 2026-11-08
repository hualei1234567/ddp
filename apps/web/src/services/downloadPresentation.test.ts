import { describe, expect, it } from "vitest";

import {
  describeDownloadFailure,
  describeExpiredDownloadLink,
  isDownloadableStatus,
  isDownloadLinkExpired,
} from "./downloadPresentation";
import { ApiError } from "./api";

function apiError(status: number, code?: string): ApiError {
  return new ApiError({
    kind: "http",
    status,
    code,
    message: "服务端拒绝",
  });
}

describe("RawAsset 下载错误与有效期", () => {
  it("403 提示权限、404 提示不可见、409 提示刷新", () => {
    const forbidden = describeDownloadFailure(apiError(403, "forbidden"));
    expect(forbidden.kind).toBe("error");
    expect(forbidden.title).toContain("无权");

    const missing = describeDownloadFailure(
      apiError(404, "resource_not_found"),
    );
    expect(missing.title).toContain("不存在");

    const conflict = describeDownloadFailure(apiError(409, "conflict"));
    expect(conflict.kind).toBe("warning");
    expect(conflict.description).toContain("刷新");
  });

  it("410 墓碑与 422 不可用分别给出稳定提示", () => {
    expect(
      describeDownloadFailure(apiError(410, "asset_tombstoned")).title,
    ).toContain("墓碑");
    expect(
      describeDownloadFailure(apiError(422, "asset_not_usable")).title,
    ).toContain("不可下载");
  });

  it("网络/5xx 可重试；未知错误不默认成功", () => {
    expect(
      describeDownloadFailure(apiError(503, "dependency_unavailable")).title,
    ).toContain("可重试");
    const unknown = describeDownloadFailure(new Error("boom"));
    expect(unknown.kind).toBe("error");
    expect(unknown.description).toContain("未识别");
  });

  it("签名链接过期判断与状态白名单", () => {
    expect(isDownloadLinkExpired(Date.now() - 1)).toBe(true);
    expect(isDownloadLinkExpired(Date.now() + 60_000)).toBe(false);
    expect(isDownloadLinkExpired(Number.NaN)).toBe(true);
    expect(describeExpiredDownloadLink().kind).toBe("warning");

    expect(isDownloadableStatus("VALID")).toBe(true);
    expect(isDownloadableStatus("ARCHIVED")).toBe(true);
    expect(isDownloadableStatus("VALIDATING")).toBe(false);
    expect(isDownloadableStatus("QUARANTINED")).toBe(false);
  });
});
