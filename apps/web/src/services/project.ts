export const PROJECT_ID_STORAGE_KEY = "ddp.project_id";

/**
 * DDP-101 §2.1：项目主键为 UUIDv7 字符串（契约允许任意 UUID 版本）。
 * 这里只校验格式，避免把空值或明显错误的输入拼进 API 路径；
 * 项目归属与权限仍由服务端校验。
 */
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isProjectId(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value.trim());
}

export interface ResolveProjectIdInput {
  /** URL 查询参数 `project_id`。 */
  query: string | null;
  /** 当前会话缓存的项目 ID。 */
  stored: string | null;
  /** 部署配置 `VITE_PROJECT_ID`。 */
  env: string | undefined;
}

/**
 * 项目上下文来源优先级：URL > 会话缓存 > 环境配置。
 * 非法值直接跳过，绝不在前端硬编码默认项目。
 */
export function resolveProjectId({
  query,
  stored,
  env,
}: ResolveProjectIdInput): string | null {
  for (const candidate of [query, stored, env]) {
    if (isProjectId(candidate)) {
      return candidate.trim().toLowerCase();
    }
  }
  return null;
}

export function readStoredProjectId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage.getItem(PROJECT_ID_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function storeProjectId(projectId: string): void {
  if (typeof window === "undefined" || !isProjectId(projectId)) {
    return;
  }
  try {
    window.sessionStorage.setItem(
      PROJECT_ID_STORAGE_KEY,
      projectId.trim().toLowerCase(),
    );
  } catch {
    // 隐私模式或存储被禁用时只影响刷新后的上下文恢复，不影响当前请求。
  }
}

/** 在页面跳转时显式携带项目上下文，避免详情页回退到其它来源。 */
export function buildProjectQuery(projectId: string | null): string {
  return projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
}
