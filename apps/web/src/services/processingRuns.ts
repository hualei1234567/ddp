/**
 * 处理运行 API（N4-001）。
 *
 * 端点（后端 `processing_routes.py`，路径前缀
 * `/api/v1/projects/{project_id}/processing-runs`）：
 * - `GET ""`：游标分页列表（`steps` 恒为 null）；
 * - `GET /{run_id}`：详情，内联有界 steps；
 * - `GET /{run_id}/steps`：完整步骤历史（游标分页）；
 * - `POST /{run_id}/actions/{cancel}`：唯一客户端动作，必须带 If-Match。
 *
 * 按钮渲染只依据服务端 `allowed_actions`，前端不按状态推断动作。
 */

import type { CursorPage } from "../types/api";
import type { ProcessingRun, ProcessingRunStep } from "../types/processing";
import { apiClient, type ApiClient } from "./api";
import { formatIfMatch, createIdempotencyKey } from "./idempotency";
import {
  parseProcessingRun,
  parseProcessingRunPage,
  parseProcessingRunStepsPage,
} from "./processingParsers";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface ProcessingRunListOptions extends PageRequestOptions {
  /** 按运行状态过滤；取值以服务端枚举为准。 */
  status?: string | null;
}

export interface ProcessingRunActionOptions {
  /** 乐观锁版本（`version` 字段），经 If-Match 下发。 */
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

function processingRunPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/processing-runs${suffix}`);
}

export interface ProcessingRunsApi {
  listRuns(
    projectId: string,
    options?: ProcessingRunListOptions,
  ): Promise<CursorPage<ProcessingRun>>;
  getRun(
    projectId: string,
    runId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ProcessingRun>;
  listRunSteps(
    projectId: string,
    runId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<ProcessingRunStep>>;
  actRun(
    projectId: string,
    runId: string,
    action: string,
    options: ProcessingRunActionOptions,
  ): Promise<ProcessingRun>;
}

export function createProcessingRunsApi(client: ApiClient): ProcessingRunsApi {
  return {
    async listRuns(projectId, options) {
      return client.get(processingRunPath(projectId), {
        signal: options?.signal,
        query: {
          status: options?.status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseProcessingRunPage(value), projectId, "处理运行列表"),
      });
    },

    async getRun(projectId, runId, options) {
      return client.get(
        processingRunPath(projectId, `/${requireIdentifier(runId, "run_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseProcessingRun(value), projectId, "处理运行详情"),
        },
      );
    },

    async listRunSteps(projectId, runId, options) {
      return client.get(
        processingRunPath(projectId, `/${requireIdentifier(runId, "run_id")}/steps`),
        {
          signal: options?.signal,
          query: pageQuery(options),
          validate: (value) => parseProcessingRunStepsPage(value),
        },
      );
    },

    async actRun(projectId, runId, action, options) {
      return client.request(
        processingRunPath(
          projectId,
          `/${requireIdentifier(runId, "run_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: {
            "If-Match": formatIfMatch(options.version),
            "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
          },
          body: {},
          validate: (value) =>
            assertProjectScope(parseProcessingRun(value), projectId, "处理运行"),
        },
      );
    },
  };
}

export const processingRunsApi = createProcessingRunsApi(apiClient);
