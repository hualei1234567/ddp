/**
 * N4-004 统一质量引擎 API。
 *
 * 三个只读追加型操作（契约 `fragments/paths.quality.yaml`）：
 * - `POST /quality-runs`：对单个 Episode / 导入执行一次检查，返回新运行；
 * - `GET /quality-runs/{run_id}`：六类聚合详情；
 * - `GET /quality-runs/{run_id}/findings`：结论明细（cursor 分页）。
 *
 * 服务端未提供"按对象列举运行"的接口，因此页面只能通过运行 ID 回看历史。
 * 前端不推断、不复制质量判定，所有结论以服务端返回为准。
 */

import type { CursorPage } from "../types/api";
import type {
  QualityFindingStatus,
  QualityRuleCategory,
  QualityRun,
  QualityRunCreateInput,
  QualityRunFinding,
} from "../types/qualityRun";
import { apiClient, type ApiClient } from "./api";
import {
  parseQualityRun,
  parseQualityRunFindingPage,
} from "./qualityRunParsers";
import {
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";
import { ContractValidationError } from "./validation";

export interface QualityRunRequestOptions {
  signal?: AbortSignal;
}

export interface QualityFindingListOptions extends PageRequestOptions {
  /** 只允许五类规则类别；`OVERALL` 是派生类别，服务端会返回空列表。 */
  category?: QualityRuleCategory | null;
  status?: QualityFindingStatus | null;
}

function runPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/quality-runs${suffix}`);
}

function runDetailPath(projectId: string, runId: string, suffix = ""): string {
  return runPath(
    projectId,
    `/${requireIdentifier(runId, "run_id")}${suffix}`,
  );
}

export interface QualityRunApi {
  createRun(
    projectId: string,
    input: QualityRunCreateInput,
    options?: QualityRunRequestOptions,
  ): Promise<QualityRun>;
  getRun(
    projectId: string,
    runId: string,
    options?: QualityRunRequestOptions,
  ): Promise<QualityRun>;
  listFindings(
    projectId: string,
    runId: string,
    options?: QualityFindingListOptions,
  ): Promise<CursorPage<QualityRunFinding>>;
}

export function createQualityRunApi(client: ApiClient): QualityRunApi {
  return {
    async createRun(projectId, input, options) {
      return client.request(runPath(projectId), {
        method: "POST",
        body: {
          subject_type: input.subject_type,
          subject_id: input.subject_id,
          categories: input.categories ?? undefined,
        },
        signal: options?.signal,
        validate: (value) => {
          const run = assertProjectScope(
            parseQualityRun(value),
            projectId,
            "质量运行",
          );
          if (run.subject_type !== input.subject_type) {
            throw new ContractValidationError(
              "质量运行.subject_type 与请求不一致",
            );
          }
          if (run.subject_id !== input.subject_id) {
            throw new ContractValidationError(
              "质量运行.subject_id 与请求不一致",
            );
          }
          return run;
        },
      });
    },
    async getRun(projectId, runId, options) {
      return client.get(runDetailPath(projectId, runId), {
        signal: options?.signal,
        validate: (value) => {
          const run = assertProjectScope(
            parseQualityRun(value),
            projectId,
            "质量运行",
          );
          if (run.run_id !== runId) {
            throw new ContractValidationError("质量运行.run_id 与请求不一致");
          }
          return run;
        },
      });
    },
    async listFindings(projectId, runId, options) {
      return client.get(runDetailPath(projectId, runId, "/findings"), {
        signal: options?.signal,
        query: {
          category: options?.category ?? undefined,
          status: options?.status ?? undefined,
          ...pageQuery(options),
        },
        validate: (value) => {
          const page = parseQualityRunFindingPage(value);
          page.items.forEach((finding, index) => {
            if (finding.run_id !== runId) {
              throw new ContractValidationError(
                `质量结论列表.items[${index}].run_id 与请求不一致`,
              );
            }
          });
          return page;
        },
      });
    },
  };
}

export const qualityRunApi: QualityRunApi = createQualityRunApi(apiClient);
