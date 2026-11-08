import { describe, expect, it, vi } from "vitest";

import { ApiError, ApiClient } from "./api";
import {
  OTHER_PROJECT_ID,
  PROJECT_ID,
  rawAssetFixture,
  TASK_ID,
  ASSET_ID,
} from "./__fixtures__/collectionFixtures";
import { createRawAssetsApi } from "./rawAssets";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

describe("阶段 2 RawAsset API client", () => {
  it("列表按状态、任务与格式过滤，并校验项目归属", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [rawAssetFixture], next_cursor: "next-asset" }),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    const page = await api.listAssets(PROJECT_ID, {
      status: "VALIDATING",
      collectionTaskId: TASK_ID,
      format: "MCAP",
    });

    expect(page.items[0]?.asset_id).toBe(ASSET_ID);
    expect(page.next_cursor).toBe("next-asset");
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/raw-assets?collection_task_id=${TASK_ID}&format=MCAP&limit=100&status=VALIDATING`,
    );
  });

  it("详情路径对 asset_id 编码并返回完整字段", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(rawAssetFixture),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    const asset = await api.getAsset(PROJECT_ID, ASSET_ID);
    expect(asset).toMatchObject({
      asset_id: ASSET_ID,
      project_id: PROJECT_ID,
      format: "MCAP",
      device_time_raw: null,
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/raw-assets/${ASSET_ID}`,
    );
  });

  it("跨项目资产响应被拒绝为 invalid_response", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ ...rawAssetFixture, project_id: OTHER_PROJECT_ID }),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    await expect(api.getAsset(PROJECT_ID, ASSET_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("列表项跨项目同样拒绝，不把外部数据交给 UI", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        items: [{ ...rawAssetFixture, project_id: OTHER_PROJECT_ID }],
        next_cursor: null,
      }),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    await expect(api.listAssets(PROJECT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("404 保持统一 ApiError 并携带 request_id", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "resource_not_found",
            message: "资源不存在或不属于当前项目",
            request_id: "rid-404",
          },
        },
        404,
      ),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    const failure = api.getAsset(PROJECT_ID, ASSET_ID);
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({
      status: 404,
      code: "resource_not_found",
      requestId: "rid-404",
    });
  });

  it("下载地址使用查询参数声明项目，并校验 URL 与有效期", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        url: "https://storage.example/raw/content.mcap?sig=short-lived",
        expires_at: "2026-11-08T12:15:00Z",
      }),
    );
    const api = createRawAssetsApi(clientWith(fetchImpl));

    const signed = await api.getDownloadUrl(PROJECT_ID, ASSET_ID);
    expect(signed.url).toContain("https://storage.example/");
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/raw-assets/${ASSET_ID}/download-url?project_id=${PROJECT_ID}`,
    );
  });

  it("下载地址拒绝非 http(s) 协议与不可解析有效期", async () => {
    const javascriptUrl = vi.fn(async () =>
      jsonResponse({
        url: "javascript:alert(1)",
        expires_at: "2026-11-08T12:15:00Z",
      }),
    );
    const api = createRawAssetsApi(clientWith(javascriptUrl));
    await expect(
      api.getDownloadUrl(PROJECT_ID, ASSET_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });

    const badExpiry = vi.fn(async () =>
      jsonResponse({
        url: "https://storage.example/raw/content.mcap",
        expires_at: "not-a-time",
      }),
    );
    const expiryApi = createRawAssetsApi(clientWith(badExpiry));
    await expect(
      expiryApi.getDownloadUrl(PROJECT_ID, ASSET_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("下载 403/404/409 保持统一错误状态，页面按状态展示", async () => {
    for (const [status, code] of [
      [403, "forbidden"],
      [404, "resource_not_found"],
      [409, "conflict"],
    ] as const) {
      const fetchImpl = vi.fn(async () =>
        jsonResponse(
          { error: { code, message: code, request_id: `rid-${status}` } },
          status,
        ),
      );
      const api = createRawAssetsApi(clientWith(fetchImpl));
      await expect(
        api.getDownloadUrl(PROJECT_ID, ASSET_ID),
      ).rejects.toMatchObject({ status, code });
    }
  });
});
