/**
 * 经营分析 API client（契约 `fragments/paths.governance.yaml`）。
 *
 * - `GET  /projects/{project_id}/usage-report`       → 用量—配额对照报表
 * - `GET  /projects/{project_id}/quotas`             → 已配置的配额（含 version）
 * - `PUT  /projects/{project_id}/quotas/{kind}`      → 设置上限（修改需 If-Match）
 *
 * 版本号来源：修改既有配额必须带 `If-Match: "<version>"`。前端用的是
 * `GET /quotas` 返回的 `version`；`PUT` 成功后服务端同时在响应体 `version`
 * 与 `ETag` 头里给出新版本，两者同值，因此后续修改直接用响应体的 `version`
 * 即可（`services/api.ts` 的 client 只返回响应体，不暴露响应头）。
 *
 * `PUT` 契约没有声明 `Idempotency-Key`（与场地 `PATCH` 同一约定），
 * 前端不额外发送，避免契约外字段。
 */

import type { Quota, QuotaList, ResourceKind, UsageReport } from "../types/governance";
import { apiClient, type ApiClient } from "./api";
import { parseQuota, parseQuotaList, parseUsageReport } from "./governanceParsers";
import { formatIfMatch } from "./idempotency";
import { assertProjectScope, projectPath, requireIdentifier } from "./projectScope";

export interface QuotaReadOptions {
  signal?: AbortSignal;
}

export interface QuotaWriteOptions {
  /**
   * 既有配额的当前版本；新建（该类型尚未配置上限）时传 `null` 或不传，
   * 服务端对「不存在 → 创建」不要求 `If-Match`。
   */
  version?: number | null;
  signal?: AbortSignal;
}

export interface GovernanceApi {
  getUsageReport(
    projectId: string,
    options?: QuotaReadOptions,
  ): Promise<UsageReport>;
  /** 只返回已配置的配额；未出现的类型代表未设上限。 */
  listQuotas(projectId: string, options?: QuotaReadOptions): Promise<Quota[]>;
  setQuota(
    projectId: string,
    resourceKind: ResourceKind,
    limit: number,
    options?: QuotaWriteOptions,
  ): Promise<Quota>;
}

function quotasPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/quotas${suffix}`);
}

function assertLimit(limit: number): number {
  // 表单已经限制，这里再守一次：把 NaN / 小数发出去只会换成 422。
  if (!Number.isSafeInteger(limit) || limit < 0) {
    throw new RangeError("配额上限必须是非负整数");
  }
  return limit;
}

export function createGovernanceApi(client: ApiClient): GovernanceApi {
  return {
    async getUsageReport(projectId, options) {
      return client.get(projectPath(projectId, "/usage-report"), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseUsageReport(value), projectId, "用量报表"),
      });
    },

    async listQuotas(projectId, options) {
      return client.get(quotasPath(projectId), {
        signal: options?.signal,
        validate: (value) => {
          const page: QuotaList = parseQuotaList(value);
          page.items.forEach((item, index) =>
            assertProjectScope(item, projectId, `配额列表.items[${index}]`),
          );
          return page.items;
        },
      });
    },

    async setQuota(projectId, resourceKind, limit, options) {
      const version = options?.version ?? null;
      return client.request(
        quotasPath(projectId, `/${requireIdentifier(resourceKind, "resource_kind")}`),
        {
          method: "PUT",
          signal: options?.signal,
          headers:
            version === null || version === undefined
              ? {}
              : { "If-Match": formatIfMatch(version) },
          body: { limit: assertLimit(limit) },
          validate: (value) =>
            assertProjectScope(parseQuota(value), projectId, "配额"),
        },
      );
    },
  };
}

export const governanceApi = createGovernanceApi(apiClient);
