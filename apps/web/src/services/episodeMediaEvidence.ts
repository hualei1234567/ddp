import { ApiError } from "./api";
import type { LineageApi } from "./lineage";
import { isPlayableMedia } from "./media";
import type { RawAssetsApi } from "./rawAssets";
import type { Episode, LineageEdge, RawAsset } from "../types/collection";

export interface MediaAssetFailure {
  assetId: string;
  error: ApiError;
}

export interface EpisodeMediaEvidence {
  /** 可直接在浏览器播放的媒体资产（视频）。 */
  playable: RawAsset[];
  /** 已关联但当前不可直接播放的资产（如 MCAP/HDF5），如实列出。 */
  notPlayable: RawAsset[];
  /** 单个资产读取失败；不影响其它资产展示。 */
  failures: MediaAssetFailure[];
  /** 反查到的来源资产总数，便于界面区分「没有来源」与「读取失败」。 */
  sourceAssetCount: number;
}

export interface EpisodeMediaDeps {
  lineage: Pick<LineageApi, "getEpisodeLineage">;
  assets: Pick<RawAssetsApi, "getAsset">;
}

/**
 * 从 Episode 反向血缘解析出上游 RawAsset ID。
 *
 * 为什么走血缘而不是 Episode 响应里的 `raw_asset_ids`：真后端 EpisodeOut
 * 不内联该字段（见 `collectionParsers` 注释），血缘端点是「这个 Episode 由
 * 哪些原始资产切出来」的权威答案。这里只认 `RAW_ASSET --SEGMENTED_INTO-->
 * EPISODE` 这一种边，不推断未经服务端确认的关系。
 */
export function extractSourceAssetIds(
  edges: readonly LineageEdge[],
  episodeId: string,
): string[] {
  const ids = new Set<string>();
  for (const edge of edges) {
    if (
      edge.from_node_type === "RAW_ASSET" &&
      edge.to_node_type === "EPISODE" &&
      edge.to_node_id === episodeId &&
      edge.relation === "SEGMENTED_INTO"
    ) {
      ids.add(edge.from_node_id);
    }
  }
  return [...ids];
}

export function splitMediaAssets(assets: readonly RawAsset[]): {
  playable: RawAsset[];
  notPlayable: RawAsset[];
} {
  const playable: RawAsset[] = [];
  const notPlayable: RawAsset[] = [];
  for (const asset of assets) {
    if (isPlayableMedia(asset.content_type)) {
      playable.push(asset);
    } else {
      notPlayable.push(asset);
    }
  }
  return { playable, notPlayable };
}

/**
 * 加载 Episode 的媒体证据。单个资产失败只记录在该资产的 failure 里，
 * 其余资产照常展示；血缘读取失败直接抛出，由调用方展示错误态。
 */
export async function loadEpisodeMediaEvidence(
  deps: EpisodeMediaDeps,
  projectId: string,
  episode: Pick<Episode, "episode_id">,
  signal: AbortSignal,
): Promise<EpisodeMediaEvidence> {
  const lineage = await deps.lineage.getEpisodeLineage(
    projectId,
    episode.episode_id,
    { direction: "reverse", depth: 1, limit: 100, signal },
  );
  const assetIds = extractSourceAssetIds(lineage.items, episode.episode_id);

  const settled = await Promise.allSettled(
    assetIds.map((assetId) =>
      deps.assets.getAsset(projectId, assetId, { signal }),
    ),
  );
  const loaded: RawAsset[] = [];
  const failures: MediaAssetFailure[] = [];
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") {
      loaded.push(result.value);
      return;
    }
    if (signal.aborted) {
      return;
    }
    failures.push({
      assetId: assetIds[index] ?? "",
      error:
        result.reason instanceof ApiError
          ? result.reason
          : new ApiError({
              kind: "network",
              message: "媒体资产加载失败",
              cause: result.reason,
            }),
    });
  });

  const { playable, notPlayable } = splitMediaAssets(loaded);
  return {
    playable,
    notPlayable,
    failures,
    sourceAssetCount: assetIds.length,
  };
}
