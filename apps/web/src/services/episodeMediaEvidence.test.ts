import { describe, expect, it, vi } from "vitest";

import type { LineageApi } from "./lineage";
import type { RawAssetsApi } from "./rawAssets";
import {
  extractSourceAssetIds,
  loadEpisodeMediaEvidence,
  splitMediaAssets,
} from "./episodeMediaEvidence";
import type { LineageEdge, RawAsset } from "../types/collection";

const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const EPISODE_ID = "33333333-3333-3333-3333-333333333333";
const ASSET_VIDEO = "44444444-4444-4444-4444-444444444444";
const ASSET_MCAP = "55555555-5555-5555-5555-555555555555";

function edge(overrides: Partial<LineageEdge> = {}): LineageEdge {
  return {
    edge_id: "66666666-6666-6666-6666-666666666666",
    project_id: PROJECT_ID,
    from_node_type: "RAW_ASSET",
    from_node_id: ASSET_VIDEO,
    to_node_type: "EPISODE",
    to_node_id: EPISODE_ID,
    relation: "SEGMENTED_INTO",
    metadata: {},
    created_by: "77777777-7777-7777-7777-777777777777",
    created_at: "2026-11-20T10:00:00Z",
    ...overrides,
  } as LineageEdge;
}

function asset(overrides: Partial<RawAsset> = {}): RawAsset {
  return {
    asset_id: ASSET_VIDEO,
    project_id: PROJECT_ID,
    code: "asset-001",
    source_type: "UPLOAD",
    device_time_raw: null,
    collection_task_id: null,
    batch_id: null,
    upload_session_id: null,
    original_filename: "cam-front.mp4",
    object_key: "project/asset-001.mp4",
    size_bytes: 2048,
    content_type: "video/mp4",
    format: "MP4",
    sha256: "a".repeat(64),
    status: "VALID",
    captured_at: null,
    ingested_at: null,
    device_ids: [],
    sensor_ids: [],
    sensor_snapshot: null,
    raw_metadata: {},
    storage_class: "STANDARD",
    retention_policy_id: null,
    quarantine_reason: null,
    quarantine_codes: [],
    scan_report_key: null,
    verified_at: null,
    archived_at: null,
    tombstoned_at: null,
    duplicate_of_asset_id: null,
    uploaded_by: "77777777-7777-7777-7777-777777777777",
    version: 1,
    created_at: "2026-11-20T10:00:00Z",
    updated_at: null,
    created_by: null,
    updated_by: null,
    allowed_actions: [],
    ...overrides,
  } as RawAsset;
}

describe("extractSourceAssetIds", () => {
  it("只认 RAW_ASSET --SEGMENTED_INTO--> 本 Episode 的边", () => {
    const ids = extractSourceAssetIds(
      [
        edge(),
        edge({ from_node_id: ASSET_MCAP }),
        // 指向别的 Episode：不能算作本 Episode 的来源。
        edge({ to_node_id: "99999999-9999-9999-9999-999999999999" }),
        // 关系不对：USED_AS_INPUT 不是切分来源。
        edge({
          relation: "USED_AS_INPUT",
          from_node_id: "88888888-8888-8888-8888-888888888888",
        }),
        // 方向反了：EPISODE -> RAW_ASSET 不能算来源。
        edge({
          from_node_type: "EPISODE",
          from_node_id: EPISODE_ID,
          to_node_type: "RAW_ASSET",
          to_node_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }),
      ],
      EPISODE_ID,
    );
    expect(ids.sort()).toEqual([ASSET_VIDEO, ASSET_MCAP].sort());
  });

  it("同一条边重复出现只返回一次", () => {
    expect(extractSourceAssetIds([edge(), edge()], EPISODE_ID)).toEqual([
      ASSET_VIDEO,
    ]);
  });
});

describe("splitMediaAssets", () => {
  it("按内容类型分流，不按扩展名猜测", () => {
    const result = splitMediaAssets([
      asset(),
      asset({
        asset_id: ASSET_MCAP,
        content_type: "application/x-mcap",
        original_filename: "run.mcap",
      }),
    ]);
    expect(result.playable.map((item) => item.asset_id)).toEqual([ASSET_VIDEO]);
    expect(result.notPlayable.map((item) => item.asset_id)).toEqual([
      ASSET_MCAP,
    ]);
  });
});

describe("loadEpisodeMediaEvidence", () => {
  function deps(overrides: {
    edges?: LineageEdge[];
    assets?: Record<string, RawAsset | Error>;
  }): {
    lineage: Pick<LineageApi, "getEpisodeLineage">;
    assets: Pick<RawAssetsApi, "getAsset">;
    getAsset: ReturnType<typeof vi.fn>;
  } {
    const getAsset = vi.fn(async (_project: string, assetId: string) => {
      const value = overrides.assets?.[assetId];
      if (value instanceof Error) {
        throw value;
      }
      if (!value) {
        throw new Error(`unexpected asset ${assetId}`);
      }
      return value;
    });
    return {
      lineage: {
        getEpisodeLineage: vi.fn(async () => ({
          items: overrides.edges ?? [],
          direction: "reverse" as const,
        })),
      },
      assets: { getAsset } as unknown as Pick<RawAssetsApi, "getAsset">,
      getAsset,
    };
  }

  it("反向血缘 → 逐个取资产 → 按可播放性分流", async () => {
    const harness = deps({
      edges: [edge(), edge({ from_node_id: ASSET_MCAP })],
      assets: {
        [ASSET_VIDEO]: asset(),
        [ASSET_MCAP]: asset({
          asset_id: ASSET_MCAP,
          content_type: "application/x-mcap",
        }),
      },
    });

    const evidence = await loadEpisodeMediaEvidence(
      harness as never,
      PROJECT_ID,
      { episode_id: EPISODE_ID },
      new AbortController().signal,
    );

    expect(harness.getAsset).toHaveBeenCalledTimes(2);
    expect(evidence.sourceAssetCount).toBe(2);
    expect(evidence.playable).toHaveLength(1);
    expect(evidence.notPlayable).toHaveLength(1);
    expect(evidence.failures).toEqual([]);
  });

  it("单个资产失败不拖垮其它资产，失败原因逐条保留", async () => {
    const harness = deps({
      edges: [edge(), edge({ from_node_id: ASSET_MCAP })],
      assets: {
        [ASSET_VIDEO]: asset(),
        [ASSET_MCAP]: new Error("boom"),
      },
    });

    const evidence = await loadEpisodeMediaEvidence(
      harness as never,
      PROJECT_ID,
      { episode_id: EPISODE_ID },
      new AbortController().signal,
    );

    expect(evidence.playable.map((item) => item.asset_id)).toEqual([
      ASSET_VIDEO,
    ]);
    expect(evidence.failures).toHaveLength(1);
    expect(evidence.failures[0]?.assetId).toBe(ASSET_MCAP);
    expect(evidence.failures[0]?.error).toBeInstanceOf(Error);
  });

  it("没有来源边时返回空的证据集合，不是错误", async () => {
    const harness = deps({ edges: [] });

    const evidence = await loadEpisodeMediaEvidence(
      harness as never,
      PROJECT_ID,
      { episode_id: EPISODE_ID },
      new AbortController().signal,
    );

    expect(harness.getAsset).not.toHaveBeenCalled();
    expect(evidence).toEqual({
      playable: [],
      notPlayable: [],
      failures: [],
      sourceAssetCount: 0,
    });
  });
});
