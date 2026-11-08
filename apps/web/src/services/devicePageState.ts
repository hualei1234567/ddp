import type { ApiError } from "./api";

/**
 * 设备页面主状态。`ready` 表示可以渲染内容（内容区再区分 Empty 与列表）。
 * 供列表页与详情页共用，避免两处各写一套优先级。
 */
export type DevicePagePhase =
  | "forbidden"
  | "not-found"
  | "loading"
  | "error"
  | "ready";

export interface DevicePagePhaseInput {
  hasData: boolean;
  loading: boolean;
  error: ApiError | null;
}

/**
 * 状态优先级遵循 UX 规范 §7.2：Forbidden/Not Found 优先于 Loading 与缓存数据，
 * 防止权限被拒后仍渲染上次成功获取的业务字段。
 */
export function resolveDevicePagePhase({
  hasData,
  loading,
  error,
}: DevicePagePhaseInput): DevicePagePhase {
  if (error?.isForbidden) {
    return "forbidden";
  }
  if (error?.isNotFound) {
    return "not-found";
  }
  if (!hasData && loading) {
    return "loading";
  }
  if (!hasData && error) {
    return "error";
  }
  return "ready";
}
