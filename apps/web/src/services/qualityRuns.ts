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
  QualityRunBatchCreateInput,
  QualityRunCreateInput,
  QualityRunDiffReport,
  QualityRunFinding,
  QualitySubjectType,
  QueuedQualityRun,
} from "../types/qualityRun";
import { apiClient, type ApiClient } from "./api";
import {
  parseQualityRun,
  parseQualityRunDiff,
  parseQualityRunFindingPage,
  parseQualityRunPage,
  parseQueuedQualityRun,
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
  /**
   * 批量重跑：为一组 Episode 各排一次质检（V31-033 / FR-026）。
   *
   * 返回的是**排队回执**（`QueuedQualityRun`，无结论），不是检查结果；
   * 空列表在前端拦截，重复对象去重后提交（服务端对重复返回 422）。
   */
  batchCreateRuns(
    projectId: string,
    input: QualityRunBatchCreateInput,
    options?: QualityRunRequestOptions,
  ): Promise<QueuedQualityRun[]>;
  /**
   * 两次运行的差异报告（V31-033 / FR-027）。
   *
   * 服务端按 `rule_id + subject_ref` 对齐并分四类；任一侧未产出结论
   * 或两次运行检查不同对象时返回 422，由调用方按 API 错误展示。
   */
  getRunDiff(
    projectId: string,
    baselineRunId: string,
    candidateRunId: string,
    options?: QualityRunRequestOptions,
  ): Promise<QualityRunDiffReport>;
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

    async batchCreateRuns(projectId, input, options) {
      // 空列表在服务端是 422；前端提前拦截，避免把「没选对象」当成
      // 「重跑完成」。重复对象去重：服务端对重复返回 422，会拒绝整批。
      const subjectIds = [...new Set(input.subject_ids)];
      if (subjectIds.length === 0) {
        throw new ContractValidationError(
          "批量重跑至少需要选择一个 Episode 对象",
        );
      }
      const queued = await client.request(runPath(projectId, "/batch"), {
        method: "POST",
        signal: options?.signal,
        body: {
          subject_type: input.subject_type,
          subject_ids: subjectIds,
        },
        validate: (value) => {
          if (!Array.isArray(value)) {
            throw new ContractValidationError("排队回执响应必须是数组");
          }
          if (value.length === 0) {
            // 契约要求 1..1000；入队 0 条的「成功」只会让人误以为重跑完成。
            throw new ContractValidationError(
              "排队回执不能是空数组（入队 0 条视为响应缺陷）",
            );
          }
          const items = value.map((item, index) =>
            assertProjectScope(
              parseQueuedQualityRun(item, `排队回执[${index}]`),
              projectId,
              "排队回执",
            ),
          );
          const requested = new Set(subjectIds);
          items.forEach((item, index) => {
            if (!requested.has(item.subject_id)) {
              throw new ContractValidationError(
                `排队回执[${index}].subject_id 不在请求的对象集合内`,
              );
            }
          });
          return items;
        },
      });
      return queued;
    },

    async getRunDiff(projectId, baselineRunId, candidateRunId, options) {
      return client.get(
        runPath(
          projectId,
          `/${requireIdentifier(baselineRunId, "baseline_run_id")}/diff/${requireIdentifier(candidateRunId, "candidate_run_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) => {
            const report = parseQualityRunDiff(value);
            if (report.baseline_run_id !== baselineRunId) {
              throw new ContractValidationError(
                "质量差异报告.baseline_run_id 与请求不一致",
              );
            }
            if (report.candidate_run_id !== candidateRunId) {
              throw new ContractValidationError(
                "质量差异报告.candidate_run_id 与请求不一致",
              );
            }
            return report;
          },
        },
      );
    },
  };
}

export const qualityRunApi: QualityRunApi = createQualityRunApi(apiClient);
