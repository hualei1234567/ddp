/** Episode 视频帧索引只读 API 客户端。 */

import type { EpisodeVideoFramesResponse } from "../types/videoFrames";
import { apiClient, type ApiClient } from "./api";
import { projectPath, requireIdentifier } from "./projectScope";
import { parseEpisodeVideoFrames } from "./videoFrameParsers";

export interface EpisodeVideoFramesQuery {
  streamId?: string;
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

export interface VideoFramesApi {
  listForEpisode: (
    projectId: string,
    episodeId: string,
    options?: EpisodeVideoFramesQuery,
  ) => Promise<EpisodeVideoFramesResponse>;
}

export function createVideoFramesApi(
  client: ApiClient = apiClient,
): VideoFramesApi {
  return {
    async listForEpisode(
      projectId: string,
      episodeId: string,
      options: EpisodeVideoFramesQuery = {},
    ): Promise<EpisodeVideoFramesResponse> {
      const safeEpisodeId = requireIdentifier(episodeId, "Episode ID");
      const query: Record<string, string | number | undefined> = {
        stream_id: options.streamId,
        offset: options.offset,
        limit: options.limit,
      };
      return client.get(
        projectPath(projectId, `/episodes/${safeEpisodeId}/video-frames`),
        {
          query,
          signal: options.signal,
          validate: parseEpisodeVideoFrames,
        },
      );
    },
  };
}

export const videoFramesApi = createVideoFramesApi();
