import { describe, expect, it, vi } from "vitest";

import {
  ASSET_PROJECT_ID,
  LOCATION_ID,
  OTHER_ASSET_PROJECT_ID,
  locationFixture,
} from "./__fixtures__/collectionAssetFixtures";
import { ApiClient } from "./api";
import { createLocationApi } from "./locations";
import { parseLocation } from "./locationParsers";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

function apiWith(body: unknown, status = 200) {
  const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse(body, status));
  return { fetchImpl, api: createLocationApi(clientWith(fetchImpl)) };
}

describe("场地响应解析", () => {
  it("解析 kind 枚举与可空字段", () => {
    const location = parseLocation(locationFixture({ notes: null }));
    expect(location.kind).toBe("SITE");
    expect(location.notes).toBeNull();
  });

  it("未识别的 kind 视为契约变更并拒绝", () => {
    expect(() =>
      parseLocation({ ...locationFixture(), kind: "WAREHOUSE" }),
    ).toThrowError(/枚举/);
  });
});

describe("场地 API client", () => {
  it("列表按 kind 过滤并校验项目归属", async () => {
    const { fetchImpl, api } = apiWith({
      items: [locationFixture()],
      next_cursor: null,
    });

    const page = await api.list(ASSET_PROJECT_ID, { kind: "ROOM" });

    expect(page.items).toHaveLength(1);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/locations?kind=ROOM&limit=100`,
    );
  });

  it("创建带幂等键，且不提交服务端权威字段", async () => {
    const { fetchImpl, api } = apiWith(locationFixture(), 201);

    await api.create(
      ASSET_PROJECT_ID,
      {
        code: "SITE-001",
        name: "上海一号基地",
        kind: "SITE",
        address: null,
        notes: null,
      },
      { idempotencyKey: "idem-loc-1" },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Idempotency-Key": "idem-loc-1" });
    expect(JSON.parse(String(init?.body))).toEqual({
      code: "SITE-001",
      name: "上海一号基地",
      kind: "SITE",
      address: null,
      notes: null,
    });
  });

  it("更新带 If-Match，路径对 ID 编码", async () => {
    const { fetchImpl, api } = apiWith(locationFixture({ version: 2 }));

    await api.update(
      ASSET_PROJECT_ID,
      LOCATION_ID,
      { name: "上海二号基地", address: null },
      { version: 1 },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/locations/${LOCATION_ID}`,
    );
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ "If-Match": '"1"' });
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "上海二号基地",
      address: null,
    });
  });

  it("跨项目响应被拒绝，前端不据此渲染", async () => {
    const { api } = apiWith({
      ...locationFixture(),
      project_id: OTHER_ASSET_PROJECT_ID,
    });

    await expect(
      api.get(ASSET_PROJECT_ID, LOCATION_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });
});
