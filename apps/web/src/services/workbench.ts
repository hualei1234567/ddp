import type { HealthResponse, PlatformMeta } from "../types/platform";
import { ApiError } from "./api";

export interface EndpointSnapshot<T> {
  data: T | null;
  error: ApiError | null;
}

export interface WorkbenchSnapshot {
  health: EndpointSnapshot<HealthResponse>;
  meta: EndpointSnapshot<PlatformMeta>;
}

function normalizeError(reason: unknown): ApiError {
  if (reason instanceof ApiError) {
    return reason;
  }
  return new ApiError({
    kind: "network",
    message: "请求失败，请检查网络或服务状态。",
    cause: reason,
  });
}

export function toEndpointSnapshot<T>(
  result: PromiseSettledResult<T>,
): EndpointSnapshot<T> {
  if (result.status === "fulfilled") {
    return { data: result.value, error: null };
  }
  return { data: null, error: normalizeError(result.reason) };
}

/** 工作台两个接口相互独立，用 allSettled 保留可用数据，支持 Partial 状态。 */
export function createWorkbenchSnapshot(
  health: PromiseSettledResult<HealthResponse>,
  meta: PromiseSettledResult<PlatformMeta>,
): WorkbenchSnapshot {
  return {
    health: toEndpointSnapshot(health),
    meta: toEndpointSnapshot(meta),
  };
}

export function hasPartialFailure(snapshot: WorkbenchSnapshot): boolean {
  return snapshot.health.error !== null || snapshot.meta.error !== null;
}

export function hasForbiddenFailure(snapshot: WorkbenchSnapshot): boolean {
  return [snapshot.health.error, snapshot.meta.error].some(
    (error) => error?.isForbidden === true,
  );
}

export function isFullyForbidden(snapshot: WorkbenchSnapshot): boolean {
  return (
    snapshot.health.error?.isForbidden === true &&
    snapshot.meta.error?.isForbidden === true
  );
}
