/** Episode 视频帧索引与时间归一化只读契约类型（V6.1）。 */

export type VideoFrameDecodeStatus =
  | "OK"
  | "DECODE_ERROR"
  | "SKIPPED"
  | (string & {});

export interface VideoFrame {
  frame_index: number;
  stream_id: string;
  pts_seconds: number | null;
  timestamp_raw: string | null;
  timestamp_normalized: number | null;
  normalize_method: string | null;
  time_base: string | null;
  is_keyframe: boolean;
  decode_status: VideoFrameDecodeStatus;
  error_message: string | null;
}

export interface EpisodeVideoFramesResponse {
  episode_id: string;
  raw_asset_ids: string[];
  total_frames: number;
  decoded_count: number;
  failed_count: number;
  normalized_count: number;
  limit: number;
  offset: number;
  items: VideoFrame[];
}
