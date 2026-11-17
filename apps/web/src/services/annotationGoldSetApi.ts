/**
 * 黄金集评估与注入接口（`/gold-sets/{id}/evaluations`、`/injections`）。
 *
 * 从 `annotationQualityApi.ts` 拆出，避免该文件突破 500 行评审线。
 * 两个端点都是写动作：带 `Idempotency-Key`，响应严格校验 `project_id`。
 */
import type { ApiClient } from "./api";
import {
  parseGoldEvaluation,
  parseGoldInjection,
  type GoldEvaluation,
  type GoldInjection,
} from "./annotationQualityParsers";
import {
  createHeaders,
  type AnnotationCreateOptions,
} from "./annotationRequestBase";
import { assertProjectScope, projectPath, requireIdentifier } from "./projectScope";

export interface GoldEvaluationCreateInput {
  task_id: string;
  submission_id: string;
}

export interface GoldInjectionCreateInput {
  task_id: string;
  annotator_id: string;
  item_ids: string[];
}

export interface AnnotationGoldSetApi {
  /** 用黄金集期望答案评估一条提交；`accuracy` 为 null 表示无可评估项。 */
  evaluateGoldSet: (
    projectId: string,
    goldSetId: string,
    input: GoldEvaluationCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<GoldEvaluation>;
  /** 向标注员注入黄金项；返回实际注入项与曝光记录 ID。 */
  injectGoldSet: (
    projectId: string,
    goldSetId: string,
    input: GoldInjectionCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<GoldInjection>;
}

function goldSetPath(projectId: string, goldSetId: string): string {
  return projectPath(
    projectId,
    `/gold-sets/${requireIdentifier(goldSetId, "gold_set_id")}`,
  );
}

export function createAnnotationGoldSetApi(
  client: ApiClient,
): AnnotationGoldSetApi {
  return {
    async evaluateGoldSet(projectId, goldSetId, input, options) {
      return client.request(`${goldSetPath(projectId, goldSetId)}/evaluations`, {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            parseGoldEvaluation(value),
            projectId,
            "黄金集评估",
          ),
      });
    },

    async injectGoldSet(projectId, goldSetId, input, options) {
      return client.request(`${goldSetPath(projectId, goldSetId)}/injections`, {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        // `GoldInjectionOut` 不含 project_id（只返回 items 与 exposure_ids），
        // 因此这里只做结构校验；项目归属由请求路径与服务端鉴权保证。
        validate: (value) => parseGoldInjection(value),
      });
    },
  };
}
