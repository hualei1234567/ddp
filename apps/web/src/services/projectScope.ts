import type { CursorPage } from "../types/api";
import { ContractValidationError } from "./validation";

/** 契约 §4.1：单页上限 100。 */
export const MAX_PAGE_LIMIT = 100;

export interface PageRequestOptions {
  signal?: AbortSignal;
  cursor?: string;
  limit?: number;
}

/** 路径参数只做空值/编码处理；权限与项目归属仍由服务端校验。 */
export function requireIdentifier(value: string, field: string): string {
  const trimmed = value.trim();
  if (trimmed === "") {
    throw new ContractValidationError(`${field} 不能为空`);
  }
  return encodeURIComponent(trimmed);
}

export function projectPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${requireIdentifier(projectId, "project_id")}${suffix}`;
}

export function pageQuery(options: PageRequestOptions | undefined): {
  cursor?: string;
  limit: number;
} {
  return {
    cursor: options?.cursor,
    limit: options?.limit ?? MAX_PAGE_LIMIT,
  };
}

export function assertProjectScope<T extends { project_id: string }>(
  item: T,
  projectId: string,
  path: string,
): T {
  if (item.project_id !== projectId) {
    throw new ContractValidationError(`${path}.project_id 与请求路径不一致`);
  }
  return item;
}

export function assertPageScope<T extends { project_id: string }>(
  page: CursorPage<T>,
  projectId: string,
  path: string,
): CursorPage<T> {
  page.items.forEach((item, index) => {
    assertProjectScope(item, projectId, `${path}.items[${index}]`);
  });
  return page;
}
