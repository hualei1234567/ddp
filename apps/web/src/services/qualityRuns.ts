/**
 * N4-004 统一质量引擎 API。
 *
 * 四个操作（契约 `fragments/paths.quality.yaml`）：
 * - `POST /quality-runs`：对单个 Episode / 导入执行一次检查，返回新运行；
 * - `GET /quality-runs?subject_type=&subject_id=`：按对象列举历史运行
 *   （创建时间/ID 倒序，cursor 分页）；
 * - `GET /quality-runs/{run_id}`：六类聚合详情；
 * - `GET /quality-runs/{run_id}/findings`：结论明细（cursor 分页）。
 *
 * 前端不推断、不复制质量判定，所有结论以服务端返回为准。
 */

import type { CursorPage } from "../types/api";
import type {
  QualityFindingStatus,
  QualityRuleCategory,
  QualityRun,
  QualityRunCreateInput,
  QualityRunFinding,
  QualitySubjectType,
} from "../types/qualityRun";
import { apiClient, type ApiClient } from "./api";
import {
  parseQualityRun,
  parseQualityRunFindingPage,
  parseQualityRunPage,
} from "./qualityRunParsers";
import {
  assertPageScope,
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

export interface QualityRunListOptions extends PageRequestOptions {
  /** 检查对象类型；服务端未知类型返回 422。 */
  subject_type: QualitySubjectType;
  /** 检查对象 ID；必填，空值在发请求前拒绝。 */
  subject_id: string;
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
  listRuns(
    projectId: string,
    options: QualityRunListOptions,
  ): Promise<CursorPage<QualityRun>>;
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
    async listRuns(projectId, options) {
      const subjectId = options.subject_id.trim();
      if (subjectId === "") {
        // 空 subject_id 会让服务端拒绝（422）；前端提前拦截，避免把
        // "参数没填"误当成"该对象没有历史运行"。
        throw new ContractValidationError("subject_id 不能为空");
      }
      return client.get(runPath(projectId), {
        signal: options.signal,
        query: {
          subject_type: options.subject_type,
          subject_id: subjectId,
          ...pageQuery(options),
        },
        validate: (value) => {
          const page = assertPageScope(
            parseQualityRunPage(value),
            projectId,
            "质量运行列表",
          );
          page.items.forEach((run, index) => {
            if (
              run.subject_type !== options.subject_type ||
              run.subject_id !== subjectId
            ) {
              throw new ContractValidationError(
                `质量运行列表.items[${index}] 与请求的检查对象不一致`,
              );
            }
          });
          return page;
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
