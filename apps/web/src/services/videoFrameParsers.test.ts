import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "./api";
import { ContractValidationError } from "./validation";
import {
  formatFrameSeconds,
  parseEpisodeVideoFrames,
  presentDecodeStatus,
} from "./videoFrameParsers";
import { createVideoFramesApi } from "./videoFrames";

const EPISODE_ID = "11111111-1111-4111-8111-111111111111";
const ASSET_ID = "22222222-2222-4222-8222-222222222222";
const PROJECT_ID = "33333333-3333-4333-8333-333333333333";

function validPayload() {
  return {
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
        stream_id: "camera_front",
        pts_seconds: null,
        timestamp_raw: null,
        timestamp_normalized: null,
        normalize_method: "NONE",
        time_base: "1/30",
        is_keyframe: false,
        decode_status: "DECODE_ERROR",
        error_message: null,
      },
    ],
  };
}

describe("videoFrameParsers", () => {
  it("解析有效的视频帧索引分页响应", () => {
    const parsed = parseEpisodeVideoFrames(validPayload());
    expect(parsed.episode_id).toBe(EPISODE_ID);
    expect(parsed.total_frames).toBe(2);
    expect(parsed.decoded_count).toBe(1);
    expect(parsed.failed_count).toBe(1);
    expect(parsed.normalized_count).toBe(1);
    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0]?.is_keyframe).toBe(true);
    expect(parsed.items[1]?.pts_seconds).toBeNull();
  });

  it("items 非数组时抛出契约校验错误", () => {
    expect(() =>
      parseEpisodeVideoFrames({ ...validPayload(), items: "invalid" }),
    ).toThrow(ContractValidationError);
  });

  it("formatFrameSeconds 正确格式化秒数与空值", () => {
    expect(formatFrameSeconds(null)).toBe("—");
    expect(formatFrameSeconds(0.5)).toBe("0.500000 s");
  });

  it("presentDecodeStatus 映射已知与未知状态", () => {
    expect(presentDecodeStatus("OK")).toEqual({
      label: "成功",
      color: "success",
    });
    expect(presentDecodeStatus("DECODE_ERROR")).toEqual({
      label: "失败",
      color: "error",
    });
    expect(presentDecodeStatus("SKIPPED")).toEqual({
      label: "跳过",
      color: "warning",
    });
    expect(presentDecodeStatus("CUSTOM")).toEqual({
      label: "CUSTOM",
      color: "default",
    });
  });
});

describe("createVideoFramesApi", () => {
  it("向项目隔离的 video-frames 端点发起 GET 请求并透传分页参数", async () => {
    const get = vi.fn().mockResolvedValue(validPayload());
    const api = createVideoFramesApi({ get } as unknown as ApiClient);

    const result = await api.listForEpisode(PROJECT_ID, EPISODE_ID, {
      streamId: "camera_front",
      offset: 20,
      limit: 50,
    });

    expect(result.total_frames).toBe(2);
    expect(get).toHaveBeenCalledWith(
      `/api/v1/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/video-frames`,
      expect.objectContaining({
        query: {
          stream_id: "camera_front",
          offset: 20,
          limit: 50,
        },
        validate: parseEpisodeVideoFrames,
      }),
    );
  });
});
