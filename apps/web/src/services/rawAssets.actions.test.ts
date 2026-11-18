/**
 * RawAsset 动作请求层测试：路径、If-Match、幂等键与请求体。
 *
 * 响应体构造一份合法的 RawAsset（含 allowed_actions），确保请求解析不会
 * 掩盖请求断言——真正的动作语义由后端用例覆盖。
 */
import { describe, expect, it, vi } from "vitest";

import {
  PROJECT_ID,
  rawAssetFixture,
} from "./__fixtures__/collectionFixtures";
import { ApiClient } from "./api";
import { createRawAssetsApi } from "./rawAssets";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function makeApi() {
  const fetchImpl = vi.fn(async () =>
    jsonResponse({ ...rawAssetFixture, status: "ARCHIVED" }),
  );
  const apiClient = new ApiClient({
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { api: createRawAssetsApi(apiClient), fetchImpl };
}

function lastCall(fetchImpl: ReturnType<typeof vi.fn>): [string, RequestInit] {
  return fetchImpl.mock.calls.at(-1) as unknown as [string, RequestInit];
}

describe("原始资产动作请求", () => {
  it("归档携带 If-Match 与幂等键", async () => {
    const { api, fetchImpl } = makeApi();
    await api.applyAction(PROJECT_ID, rawAssetFixture.asset_id, "archive", {
      version: 3,
      idempotencyKey: "asset-action-key-0001",
    });
    const [url, init] = lastCall(fetchImpl);
    expect(url).toContain(
      `/api/v1/projects/${PROJECT_ID}/raw-assets/${rawAssetFixture.asset_id}/actions/archive`,
    );
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"3"');
    expect(headers["Idempotency-Key"]).toBe("asset-action-key-0001");
    expect(JSON.parse(String(init.body))).toMatchObject({
      reason: null,
      quarantine_codes: null,
    });
  });

  it("事后隔离提交原因与隔离码", async () => {
    const { api, fetchImpl } = makeApi();
    await api.applyAction(
      PROJECT_ID,
      rawAssetFixture.asset_id,
      "post-hoc-quarantine",
      {
        version: 1,
        reason: "发现隐私画面",
        quarantineCodes: ["metadata_conflict"],
        idempotencyKey: "asset-action-key-0002",
      },
    );
    const [, init] = lastCall(fetchImpl);
    expect(JSON.parse(String(init.body))).toMatchObject({
      reason: "发现隐私画面",
      quarantine_codes: ["metadata_conflict"],
    });
  });

  it("返回体缺少 allowed_actions 时视为契约不满足", async () => {
    const withoutActions: Record<string, unknown> = { ...rawAssetFixture };
    delete withoutActions.allowed_actions;
    const fetchImpl = vi.fn(async () => jsonResponse(withoutActions));
    const api = createRawAssetsApi(
      new ApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch }),
    );
    // 客户端对外只回固定文案（不回显字段名/响应体），因此断言的是"被拒绝"
    // 而不是某个具体字符串。
    await expect(
      api.applyAction(PROJECT_ID, rawAssetFixture.asset_id, "archive", {
        version: 1,
      }),
    ).rejects.toThrow("服务返回的数据结构不符合契约");
  });
});
