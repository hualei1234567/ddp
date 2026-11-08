import { describe, expect, it, vi } from "vitest";

import {
  ASSET_ID,
  EPISODE_ID,
  OTHER_PROJECT_ID,
  PROJECT_ID,
  USER_ID,
} from "./__fixtures__/collectionFixtures";
import { ApiClient } from "./api";
import { createLineageApi } from "./lineage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

function edge(overrides: Record<string, unknown> = {}) {
  return {
    edge_id: "66666666-6666-7666-8666-666666666666",
    project_id: PROJECT_ID,
    from_node_type: "RAW_ASSET",
    from_node_id: ASSET_ID,
    to_node_type: "EPISODE",
    to_node_id: EPISODE_ID,
    relation: "SEGMENTED_INTO",
    metadata: {},
    created_by: USER_ID,
    created_at: "2026-11-08T03:30:00Z",
    ...overrides,
  };
}

describe("Episode / RawAsset 血缘 API", () => {
  it("正反向查询使用查询参数声明项目，并校验方向回显", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [edge()], direction: "forward" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [edge()], direction: "reverse" }),
      );
    const api = createLineageApi(clientWith(fetchImpl));

    const forward = await api.getEpisodeLineage(PROJECT_ID, EPISODE_ID, {
      direction: "forward",
    });
    expect(forward.items).toHaveLength(1);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/episodes/${EPISODE_ID}/lineage?depth=1&direction=forward&limit=100&project_id=${PROJECT_ID}`,
    );

    await api.getEpisodeLineage(PROJECT_ID, EPISODE_ID, {
      direction: "reverse",
      depth: 3,
    });
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/episodes/${EPISODE_ID}/lineage?depth=3&direction=reverse&limit=100&project_id=${PROJECT_ID}`,
    );
  });

  it("RawAsset 血缘路径不带 projects 前缀", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [], direction: "reverse" }),
    );
    const api = createLineageApi(clientWith(fetchImpl));

    await api.getRawAssetLineage(PROJECT_ID, ASSET_ID, {
      direction: "reverse",
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/raw-assets/${ASSET_ID}/lineage?depth=1&direction=reverse&limit=100&project_id=${PROJECT_ID}`,
    );
  });

  it("跨项目节点或方向不一致直接拒绝为 invalid_response", async () => {
    const crossProject = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        items: [edge({ project_id: OTHER_PROJECT_ID })],
        direction: "forward",
      }),
    );
    const api = createLineageApi(clientWith(crossProject));
    await expect(
      api.getEpisodeLineage(PROJECT_ID, EPISODE_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });

    const wrongDirection = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [], direction: "reverse" }),
    );
    const wrongApi = createLineageApi(clientWith(wrongDirection));
    await expect(
      wrongApi.getEpisodeLineage(PROJECT_ID, EPISODE_ID, {
        direction: "forward",
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });
});
