import type {
  LineageDirection,
  LineageResponse,
} from "../types/collection";
import { apiClient, type ApiClient } from "./api";
import { parseLineageResponse } from "./collectionParsers";
import { assertProjectScope, requireIdentifier } from "./projectScope";

export interface LineageQueryOptions {
  direction?: LineageDirection;
  /** 契约允许 1–5；默认只取相邻一层。 */
  depth?: number;
  limit?: number;
  signal?: AbortSignal;
}

export interface LineageApi {
  getEpisodeLineage: (
    projectId: string,
    episodeId: string,
    options?: LineageQueryOptions,
  ) => Promise<LineageResponse>;
  getRawAssetLineage: (
    projectId: string,
    assetId: string,
    options?: LineageQueryOptions,
  ) => Promise<LineageResponse>;
}

/**
 * 血缘端点不带 `/projects/{project_id}` 前缀，项目上下文通过查询参数
 * 声明；服务端仍按成员资格与资源归属双重校验。前端逐条校验 project_id。
 */
function lineageQuery(
  projectId: string,
  options: LineageQueryOptions | undefined,
  direction: LineageDirection,
): Record<string, string | number> {
  return {
    project_id: projectId,
    direction,
    depth: options?.depth ?? 1,
    limit: options?.limit ?? 100,
  };
}

function validateLineage(
  value: unknown,
  projectId: string,
  direction: LineageDirection,
): LineageResponse {
  const response = parseLineageResponse(value, direction);
  response.items.forEach((edge, index) =>
    assertProjectScope(edge, projectId, `血缘查询.items[${index}]`),
  );
  return response;
}

export function createLineageApi(client: ApiClient): LineageApi {
  return {
    async getEpisodeLineage(projectId, episodeId, options = {}) {
      const direction = options.direction ?? "forward";
      return client.get(
        `/api/v1/episodes/${requireIdentifier(episodeId, "episode_id")}/lineage`,
        {
          signal: options.signal,
          query: lineageQuery(projectId, options, direction),
          validate: (value) =>
            validateLineage(value, projectId, direction),
        },
      );
    },

    async getRawAssetLineage(projectId, assetId, options = {}) {
      const direction = options.direction ?? "forward";
      return client.get(
        `/api/v1/raw-assets/${requireIdentifier(assetId, "asset_id")}/lineage`,
        {
          signal: options.signal,
          query: lineageQuery(projectId, options, direction),
          validate: (value) =>
            validateLineage(value, projectId, direction),
        },
      );
    },
  };
}

export const lineageApi = createLineageApi(apiClient);
