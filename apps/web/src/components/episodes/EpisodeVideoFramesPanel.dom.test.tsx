import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../services/api";
import type { VideoFramesApi } from "../../services/videoFrames";
import type { EpisodeVideoFramesResponse } from "../../types/videoFrames";
import EpisodeVideoFramesPanel from "./EpisodeVideoFramesPanel";

const PROJECT_ID = "33333333-3333-4333-8333-333333333333";
const EPISODE_ID = "11111111-1111-4111-8111-111111111111";
const ASSET_ID = "22222222-2222-4222-8222-222222222222";

const sampleResponse: EpisodeVideoFramesResponse = {
  episode_id: EPISODE_ID,
  raw_asset_ids: [ASSET_ID],
  total_frames: 2,
  decoded_count: 1,
  failed_count: 1,
  normalized_count: 1,
  limit: 20,
  offset: 0,
  items: [
    {
      frame_index: 0,
      stream_id: "camera_front",
      pts_seconds: 0.033333,
      timestamp_raw: "1",
      timestamp_normalized: 0.033333,
      normalize_method: "SECONDS_IDENTITY",
      time_base: "1/30",
      is_keyframe: true,
      decode_status: "OK",
      error_message: null,
    },
    {
      frame_index: 1,
      stream_id: "camera_wrist",
      pts_seconds: null,
      timestamp_raw: null,
      timestamp_normalized: null,
      normalize_method: "NONE",
      time_base: "1/30",
      is_keyframe: false,
      decode_status: "DECODE_ERROR",
      error_message: "decoder error",
    },
  ],
};

describe("EpisodeVideoFramesPanel", () => {
  it("展示帧数统计、解码状态分布、关键帧标记与 PTS/归一化时间对比表", async () => {
    const api: VideoFramesApi = {
      listForEpisode: vi.fn().mockResolvedValue(sampleResponse),
    };

    render(
      <EpisodeVideoFramesPanel
        projectId={PROJECT_ID}
        episodeId={EPISODE_ID}
        api={api}
      />,
    );

    expect(await screen.findByText("camera_front")).toBeInTheDocument();
    expect(screen.getByText("camera_wrist")).toBeInTheDocument();
    expect(screen.getByText("总帧数")).toBeInTheDocument();
    expect(screen.getByText("解码成功")).toBeInTheDocument();
    expect(screen.getByText("解码失败")).toBeInTheDocument();
    expect(screen.getByText("已时间归一化")).toBeInTheDocument();
    expect(screen.getAllByText("关键帧").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("普通帧")).toBeInTheDocument();
    expect(screen.getByText("成功 1（50%）")).toBeInTheDocument();
    expect(screen.getByText("失败 1（50%）")).toBeInTheDocument();
    expect(screen.getAllByText("0.033333 s")).toHaveLength(2);
    expect(screen.getByText("SECONDS_IDENTITY")).toBeInTheDocument();
    expect(screen.getByText("decoder error")).toBeInTheDocument();
  });

  it("无帧索引时展示诚实空态", async () => {
    const api: VideoFramesApi = {
      listForEpisode: vi.fn().mockResolvedValue({
        episode_id: EPISODE_ID,
        raw_asset_ids: [],
        total_frames: 0,
        decoded_count: 0,
        failed_count: 0,
        normalized_count: 0,
        limit: 20,
        offset: 0,
        items: [],
      }),
    };

    render(
      <EpisodeVideoFramesPanel
        projectId={PROJECT_ID}
        episodeId={EPISODE_ID}
        api={api}
      />,
    );

    expect(await screen.findByText("暂无视频帧索引")).toBeInTheDocument();
    expect(
      screen.getByText("当前 Episode 尚未关联原始视频资产，帧数汇总为 0。"),
    ).toBeInTheDocument();
  });

  it("请求失败时展示错误状态并支持重试", async () => {
    const user = userEvent.setup();
    const listForEpisode = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError({ kind: "network", message: "网络不可达" }),
      )
      .mockResolvedValueOnce(sampleResponse);

    render(
      <EpisodeVideoFramesPanel
        projectId={PROJECT_ID}
        episodeId={EPISODE_ID}
        api={{ listForEpisode }}
      />,
    );

    expect(await screen.findByText("网络不可达")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("camera_front")).toBeInTheDocument();
    expect(listForEpisode).toHaveBeenCalledTimes(2);
  });
});
