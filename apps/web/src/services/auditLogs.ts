/**
 * 审计日志只读 API client。
 *
 * 只有列表与资源时间线；后端不提供任何写入口，这里也不虚构 create/delete。
 * 访问权限由服务端判定（项目负责人或平台管理员），前端不做权威校验。
 */

import type { CursorPage } from "../types/api";
import type { AuditLog, AuditLogListFilters } from "../types/auditLog";
import { apiClient, type ApiClient } from "./api";
import { parseAuditLogPage } from "./auditLogParsers";
import {
  assertPageScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface AuditLogListOptions extends PageRequestOptions {
  filters?: AuditLogListFilters;
}

export interface AuditLogApi {
  list(
    projectId: string,
    options?: AuditLogListOptions,
  ): Promise<CursorPage<AuditLog>>;
  timeline(
    projectId: string,
    resourceType: string,
    resourceId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<AuditLog>>;
}

function auditLogPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/audit-logs${suffix}`);
}

export function createAuditLogApi(client: ApiClient): AuditLogApi {
  return {
    async list(projectId, options) {
      return client.get(auditLogPath(projectId), {
        signal: options?.signal,
        query: { ...options?.filters, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseAuditLogPage(value), projectId, "审计日志"),
      });
    },

    async timeline(projectId, resourceType, resourceId, options) {
      const suffix = `/${requireIdentifier(resourceType, "resource_type")}/${requireIdentifier(
        resourceId,
        "resource_id",
      )}`;
      return client.get(auditLogPath(projectId, suffix), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseAuditLogPage(value), projectId, "资源时间线"),
      });
    },
  };
}

export const auditLogApi = createAuditLogApi(apiClient);
