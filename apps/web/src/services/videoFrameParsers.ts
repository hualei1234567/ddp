/** Episode 视频帧索引响应解析与展示工具。 */

import type {
  EpisodeVideoFramesResponse,
  VideoFrame,
} from "../types/videoFrames";
import {
  asRecord,
  ContractValidationError,
  readBoolean,
  readInteger,
  readOptionalNumber,
  readOptionalString,
  readPositiveInteger,
  readString,
  readUuid,
  readUuidArray,
} from "./validation";

export function parseVideoFrame(
  value: unknown,
  path = "videoFrame",
): VideoFrame {
  const record = asRecord(value, path);
  return {
    frame_index: readInteger(record, "frame_index", `${path}.frame_index`),
    stream_id: readString(record, "stream_id", `${path}.stream_id`),
    pts_seconds: readOptionalNumber(
      record,
      "pts_seconds",
      `${path}.pts_seconds`,
    ),
    timestamp_raw: readOptionalString(
      record,
      "timestamp_raw",
      `${path}.timestamp_raw`,
    ),
    timestamp_normalized: readOptionalNumber(
      record,
      "timestamp_normalized",
      `${path}.timestamp_normalized`,
    ),
    normalize_method: readOptionalString(
      record,
      "normalize_method",
      `${path}.normalize_method`,
    ),
    time_base: readOptionalString(record, "time_base", `${path}.time_base`),
    is_keyframe: readBoolean(record, "is_keyframe", `${path}.is_keyframe`),
    decode_status: readString(record, "decode_status", `${path}.decode_status`),
    error_message: readOptionalString(
      record,
      "error_message",
      `${path}.error_message`,
    ),
  };
}

export function parseEpisodeVideoFrames(
  value: unknown,
  path = "episodeVideoFrames",
): EpisodeVideoFramesResponse {
  const record = asRecord(value, path);
  const rawItems = record.items;
  if (!Array.isArray(rawItems)) {
    throw new ContractValidationError(`${path}.items 必须是数组`);
  }
  return {
    episode_id: readUuid(record, "episode_id", `${path}.episode_id`),
    raw_asset_ids: readUuidArray(
      record,
      "raw_asset_ids",
      `${path}.raw_asset_ids`,
    ),
    total_frames: readInteger(record, "total_frames", `${path}.total_frames`),
    decoded_count: readInteger(
      record,
      "decoded_count",
      `${path}.decoded_count`,
    ),
    failed_count: readInteger(record, "failed_count", `${path}.failed_count`),
    normalized_count: readInteger(
      record,
      "normalized_count",
      `${path}.normalized_count`,
    ),
    limit: readPositiveInteger(record, "limit", `${path}.limit`),
    offset: readInteger(record, "offset", `${path}.offset`),
    items: rawItems.map((item, index) =>
      parseVideoFrame(item, `${path}.items[${index}]`),
    ),
  };
}

/** 格式化秒级时间戳（保留最多 6 位小数，null 原样返回占位符）。 */
export function formatFrameSeconds(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return `${value.toFixed(6)} s`;
}

export interface DecodeStatusPresentation {
  label: string;
  color: string;
}

export function presentDecodeStatus(status: string): DecodeStatusPresentation {
  if (status === "OK") {
    return { label: "成功", color: "success" };
  }
  if (status === "DECODE_ERROR") {
    return { label: "失败", color: "error" };
  }
  if (status === "SKIPPED") {
    return { label: "跳过", color: "warning" };
  }
  return { label: status, color: "default" };
}
