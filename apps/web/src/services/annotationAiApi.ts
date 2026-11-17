/**
 * AI 预标注建议接口（由 `annotation.ts` 组合）。
 *
 * 覆盖三个前端可用的端点：
 * - `GET  /annotation-submissions/{id}/ai-suggestions`：建议列表；
 * - `POST /annotation-ai-suggestions/{id}/confirm`：采纳 / 修订（带 `If-Match`）；
 * - `GET  /annotation-submissions/{id}/ai-suggestions/agreement`：按模型版本的一致率。
 *
 * 登记建议（`POST ai-suggestions`）由模型侧 pipeline 调用，不在页面开放。
 */
import type { CursorPage } from "../types/api";
import type { ApiClient } from "./api";
import {
  parseAiSuggestion,
  parseAiSuggestionPage,
  parseSuggestionAgreement,
  type AiSuggestion,
  type SuggestionAgreement,
} from "./annotationAiParsers";
import {
  writeHeaders,
  type AnnotationWriteOptions,
  type DetailRequestOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  projectPath,
  requireIdentifier,
} from "./projectScope";

export interface AnnotationAiApi {
  listAiSuggestions: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<CursorPage<AiSuggestion>>;
  /**
   * 人工采纳或修订建议。
   *
   * `humanValue` 与建议值相同即「采纳」，不同即「修订」——后端按值判断，
   * 前端不额外发送一个语义字段。
   */
  confirmAiSuggestion: (
    projectId: string,
    suggestionId: string,
    humanValue: unknown,
    options: AnnotationWriteOptions,
  ) => Promise<AiSuggestion>;
  getSuggestionAgreement: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<SuggestionAgreement>;
}

function submissionAiPath(
  projectId: string,
  submissionId: string,
  suffix = "",
): string {
  return projectPath(
    projectId,
    `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/ai-suggestions${suffix}`,
  );
}

export function createAnnotationAiApi(client: ApiClient): AnnotationAiApi {
  return {
    async listAiSuggestions(projectId, submissionId, options) {
      return client.get(submissionAiPath(projectId, submissionId), {
        signal: options?.signal,
        validate: (value) =>
          assertPageScope(
            parseAiSuggestionPage(value),
            projectId,
            "AI 建议列表",
          ),
      });
    },

    async confirmAiSuggestion(projectId, suggestionId, humanValue, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-ai-suggestions/${requireIdentifier(suggestionId, "suggestion_id")}/confirm`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { human_value: humanValue },
          validate: (value) =>
            assertProjectScope(
              parseAiSuggestion(value),
              projectId,
              "AI 建议",
            ),
        },
      );
    },

    async getSuggestionAgreement(projectId, submissionId, options) {
      return client.get(submissionAiPath(projectId, submissionId, "/agreement"), {
        signal: options?.signal,
        validate: (value) => parseSuggestionAgreement(value),
      });
    },
  };
}
