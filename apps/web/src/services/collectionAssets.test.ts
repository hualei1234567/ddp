import { describe, expect, it, vi } from "vitest";

import {
  ACTION_SPEC_ID,
  ASSET_PROJECT_ID,
  OTHER_ASSET_PROJECT_ID,
  PLAN_ID,
  SOP_ID,
  actionSpecFixture,
  planFixture,
  scenarioFixture,
  sopFixture,
} from "./__fixtures__/collectionAssetFixtures";
import { ApiClient } from "./api";
import {
  createCollectionAssetApi,
  isCollectionAssetKind,
} from "./collectionAssets";

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
  return { fetchImpl, api: createCollectionAssetApi(clientWith(fetchImpl)) };
}

describe("采集素材 API client", () => {
  it("按 kind 组装资源路径并用服务端 status 过滤", async () => {
    const { fetchImpl, api } = apiWith({
      items: [planFixture()],
      next_cursor: null,
    });

    const page = await api.list("plans", ASSET_PROJECT_ID, { status: "DRAFT" });

    expect(page.items).toHaveLength(1);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/collection-plans?limit=100&status=DRAFT`,
    );
  });

  it("四类素材的资源段与契约一致", async () => {
    const cases = [
      { kind: "plans", body: planFixture(), segment: "collection-plans" },
      { kind: "sops", body: sopFixture(), segment: "collection-sops" },
      { kind: "scenarios", body: scenarioFixture(), segment: "scenarios" },
      {
        kind: "action-specs",
        body: actionSpecFixture(),
        segment: "action-specs",
      },
    ] as const;

    for (const item of cases) {
      const { fetchImpl, api } = apiWith({
        items: [item.body],
        next_cursor: null,
      });
      await api.list(item.kind, ASSET_PROJECT_ID);
      expect(fetchImpl.mock.calls[0]?.[0]).toBe(
        `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/${item.segment}?limit=100`,
      );
    }
  });

  it("创建只提交契约允许字段并携带幂等键", async () => {
    const { fetchImpl, api } = apiWith(planFixture(), 201);

    await api.create(
      "plans",
      ASSET_PROJECT_ID,
      {
        code: "PLAN-001",
        name: "仓储拣选采集方案",
        plan_version: 1,
        description: null,
      },
      { idempotencyKey: "idem-plan-001" },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Idempotency-Key": "idem-plan-001" });
    expect(JSON.parse(String(init?.body))).toEqual({
      code: "PLAN-001",
      name: "仓储拣选采集方案",
      plan_version: 1,
      description: null,
    });
  });

  it("PATCH 带 If-Match，且不额外发送幂等键", async () => {
    const { fetchImpl, api } = apiWith(planFixture({ version: 4 }));

    await api.update(
      "plans",
      ASSET_PROJECT_ID,
      PLAN_ID,
      { name: "改名后的方案", description: null },
      { version: 3 },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/collection-plans/${PLAN_ID}`,
    );
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ "If-Match": '"3"' });
    expect(
      (init?.headers as Record<string, string>)["Idempotency-Key"],
    ).toBeUndefined();
  });

  it("状态动作带 If-Match、幂等键与可选原因", async () => {
    const { fetchImpl, api } = apiWith(sopFixture({ status: "ACTIVE" }));

    await api.act("sops", ASSET_PROJECT_ID, SOP_ID, "archive", {
      version: 3,
      reason: "内容过期",
      idempotencyKey: "idem-act-1",
    });

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${ASSET_PROJECT_ID}/collection-sops/${SOP_ID}/actions/archive`,
    );
    expect(init?.headers).toMatchObject({
      "If-Match": '"3"',
      "Idempotency-Key": "idem-act-1",
    });
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "内容过期" });
  });

  it("删除使用 DELETE 并接受 204 空响应", async () => {
    const fetchImpl = vi.fn<typeof fetch>(
      async () => new Response(null, { status: 204 }),
    );
    const api = createCollectionAssetApi(clientWith(fetchImpl));

    await expect(
      api.remove("action-specs", ASSET_PROJECT_ID, ACTION_SPEC_ID),
    ).resolves.toBeUndefined();
    expect(fetchImpl.mock.calls[0]?.[1]?.method).toBe("DELETE");
  });

  it("跨项目响应被拒绝为 invalid_response", async () => {
    const { api } = apiWith({
      ...planFixture(),
      project_id: OTHER_ASSET_PROJECT_ID,
    });

    await expect(
      api.get("plans", ASSET_PROJECT_ID, PLAN_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("只接受四个已知的素材 kind", () => {
    expect(isCollectionAssetKind("plans")).toBe(true);
    expect(isCollectionAssetKind("action-specs")).toBe(true);
    expect(isCollectionAssetKind("unknown")).toBe(false);
  });
});
