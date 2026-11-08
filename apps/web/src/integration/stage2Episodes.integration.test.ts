import { beforeAll, describe, expect, it } from "vitest";

import type { Episode } from "../types/collection";
import { ApiClient } from "../services/api";
import {
  createCollectionsApi,
  type CollectionsApi,
} from "../services/collections";
import { createLineageApi, type LineageApi } from "../services/lineage";
import { createRawAssetsApi, type RawAssetsApi } from "../services/rawAssets";

/**
 * Episode / 血缘 / 下载真实后端集成测试。
 *
 * 默认单测不会连接外部环境：只有显式设置 DDP_IT_BASE_URL、
 * DDP_IT_PROJECT_ID、DDP_IT_USER_ID 时才执行；否则整组跳过。
 * 只读断言，不创建/修改业务数据，可重复运行。
 */

function readEnv(name: string): string {
  const runtime = globalThis as {
    process?: { env?: Record<string, string | undefined> };
  };
  return runtime.process?.env?.[name]?.trim() ?? "";
}

const BASE_URL = readEnv("DDP_IT_BASE_URL");
const PROJECT_ID = readEnv("DDP_IT_PROJECT_ID");
const USER_ID = readEnv("DDP_IT_USER_ID");

if (BASE_URL !== "" && (PROJECT_ID === "" || USER_ID === "")) {
  throw new Error(
    "DDP_IT_BASE_URL 已设置，但缺少 DDP_IT_PROJECT_ID 或 DDP_IT_USER_ID",
  );
}

const integration = BASE_URL === "" ? describe.skip : describe;

function requireEnv(name: string, value: string): string {
  if (value === "") {
    throw new Error(`集成测试缺少环境变量：${name}`);
  }
  return value;
}

function createAuthenticatedClient(): ApiClient {
  const userId = requireEnv("DDP_IT_USER_ID", USER_ID);
  return new ApiClient({
    baseUrl: requireEnv("DDP_IT_BASE_URL", BASE_URL),
    timeoutMs: 30_000,
    fetchImpl: (input, init) => {
      const headers = new Headers(init?.headers);
      headers.set("X-User-Id", userId);
      return fetch(input, { ...init, headers });
    },
  });
}

let collectionsApi!: CollectionsApi;
let lineageApi!: LineageApi;
let rawAssetsApi!: RawAssetsApi;
let firstEpisode: Episode | null = null;
let listError: unknown = null;

integration("DDP-314 真实后端 Episode/血缘/下载只读联调", () => {
  beforeAll(async () => {
    const client = createAuthenticatedClient();
    collectionsApi = createCollectionsApi(client);
    lineageApi = createLineageApi(client);
    rawAssetsApi = createRawAssetsApi(client);
    try {
      const page = await collectionsApi.listEpisodes(PROJECT_ID);
      firstEpisode = page.items[0] ?? null;
    } catch (caught) {
      // 保留错误让首个用例给出准确失败原因；无数据不算失败。
      listError = caught;
      firstEpisode = null;
    }
  });

  it("Episode 列表接口可重复读取并按项目校验", async () => {
    if (listError) {
      throw listError;
    }
    const page = await collectionsApi.listEpisodes(PROJECT_ID);
    expect(page.items.every((item) => item.project_id === PROJECT_ID)).toBe(
      true,
    );
  });

  it("Episode 详情与正反向血缘", async (context) => {
    if (!firstEpisode) {
      context.skip();
      return;
    }
    const detail = await collectionsApi.getEpisode(
      PROJECT_ID,
      firstEpisode.episode_id,
    );
    expect(detail.episode_id).toBe(firstEpisode.episode_id);
    expect(detail.project_id).toBe(PROJECT_ID);

    for (const direction of ["forward", "reverse"] as const) {
      const lineage = await lineageApi.getEpisodeLineage(
        PROJECT_ID,
        firstEpisode.episode_id,
        { direction, depth: 1 },
      );
      expect(lineage.direction).toBe(direction);
      expect(
        lineage.items.every((edge) => edge.project_id === PROJECT_ID),
      ).toBe(true);
    }
  });

  it("上游 RawAsset 可获取短期下载地址", async (context) => {
    if (!firstEpisode) {
      context.skip();
      return;
    }
    const lineage = await lineageApi.getEpisodeLineage(
      PROJECT_ID,
      firstEpisode.episode_id,
      { direction: "reverse", depth: 2 },
    );
    const assetId = lineage.items.find(
      (edge) => edge.from_node_type === "RAW_ASSET",
    )?.from_node_id;
    if (!assetId) {
      context.skip();
      return;
    }
    const signed = await rawAssetsApi.getDownloadUrl(PROJECT_ID, assetId);
    expect(signed.url).toMatch(/^https?:\/\//);
    expect(Date.parse(signed.expires_at)).toBeGreaterThan(Date.now());
  });

  it("随机不存在的 Episode 统一返回 404 语义", async () => {
    const missingId = crypto.randomUUID();
    await expect(
      collectionsApi.getEpisode(PROJECT_ID, missingId),
    ).rejects.toMatchObject({ status: 404 });
    await expect(
      lineageApi.getEpisodeLineage(PROJECT_ID, missingId),
    ).rejects.toMatchObject({ status: 404 });
  });
});
