import { ApiError } from "./api";

/** 媒体审看所需的最小资产视图；不绑定完整 RawAsset，便于跨模块复用。 */
export interface MediaAssetRef {
  assetId: string;
  originalFilename: string;
  contentType: string | null;
  sizeBytes: number;
}

const PLAYABLE_PREFIX = "video/";

/** 只有视频内容类型可直接播放；其余格式如实说明，不伪装成可播。 */
export function isPlayableMedia(
  contentType: string | null | undefined,
): boolean {
  return (
    typeof contentType === "string" && contentType.startsWith(PLAYABLE_PREFIX)
  );
}

/** 秒 → `mm:ss.S`；非法值收敛为占位，避免把 NaN 渲染到界面。 */
export function formatPlaybackClock(seconds: number): string {
  if (!Number.isFinite(seconds)) {
    return "--:--.-";
  }
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

/**
 * Episode 起止纳秒 → 秒时长。
 *
 * jsdom 与部分浏览器在 metadata 未就绪时不提供 `video.duration`，
 * 因此时长以服务端 Episode 边界为准，播放器自身时长只做兜底。
 */
export function durationFromNs(
  startTimeNs: string | number | null | undefined,
  endTimeNs: string | number | null | undefined,
): number | null {
  if (startTimeNs == null || endTimeNs == null) {
    return null;
  }
  const start = Number(startTimeNs);
  const end = Number(endTimeNs);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return null;
  }
  return (end - start) / 1_000_000_000;
}

/** 绝对纳秒 → 相对 Episode 起点的秒数，供播放器定位。 */
export function nsToRelativeSeconds(
  timeNs: string | number | null | undefined,
  startTimeNs: string | number | null | undefined,
): number | null {
  if (timeNs == null || startTimeNs == null) {
    return null;
  }
  const value = Number(timeNs);
  const start = Number(startTimeNs);
  if (!Number.isFinite(value) || !Number.isFinite(start)) {
    return null;
  }
  return (value - start) / 1_000_000_000;
}

/** 相对秒 → 绝对纳秒，把播放器选点还原成可提交的标签时间。 */
export function relativeSecondsToNs(
  seconds: number,
  startTimeNs: string | number | null | undefined,
): number | null {
  if (!Number.isFinite(seconds) || startTimeNs == null) {
    return null;
  }
  const start = Number(startTimeNs);
  if (!Number.isFinite(start)) {
    return null;
  }
  return Math.round(start + seconds * 1_000_000_000);
}

export function formatMediaSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "未知大小";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 网络层异常统一成 ApiError，保证错误态展示与重试路径一致。 */
export function asMediaError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({
        kind: "network",
        message: "无法获取播放地址",
        cause: caught,
      });
}
