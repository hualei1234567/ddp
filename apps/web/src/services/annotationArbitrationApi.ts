/**
 * 标注仲裁与投票接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-submissions/{id}/arbitrations`、`/annotation-arbitrations`
 * 与 `votes` 子资源。投票是写动作，同样要 If-Match + 幂等键。
 */
import type { CursorPage } from "../types/api";
import type {
  AnnotationArbitration,
  ArbitrationDecision,
} from "../types/annotation";
import type { ApiClient } from "./api";
import {
  parseAnnotationArbitration,
  parseAnnotationArbitrationPage,
} from "./annotationResultParsers";
import {
  createHeaders,
  writeHeaders,
  type AnnotationCreateOptions,
  type AnnotationWriteOptions,
  type ArbitrationCreateInput,
  type DetailRequestOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  projectPath,
  requireIdentifier,
} from "./projectScope";

export interface AnnotationArbitrationApi {
  listSubmissionArbitrations: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<CursorPage<AnnotationArbitration>>;
  createArbitrationForSubmission: (
    projectId: string,
    submissionId: string,
    input: Omit<ArbitrationCreateInput, "submissionId">,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationArbitration>;
  createArbitration: (
    projectId: string,
    input: ArbitrationCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationArbitration>;
  getArbitration: (
    projectId: string,
    arbitrationId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationArbitration>;
  actOnArbitration: (
    projectId: string,
    arbitrationId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<AnnotationArbitration>;
  voteOnArbitration: (
    projectId: string,
    arbitrationId: string,
    input: { decision: ArbitrationDecision; reason?: string | null },
    options: AnnotationWriteOptions,
  ) => Promise<AnnotationArbitration>;
}

export function createAnnotationArbitrationApi(
  client: ApiClient,
): AnnotationArbitrationApi {
  return {
    async listSubmissionArbitrations(projectId, submissionId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/arbitrations`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertPageScope(
              parseAnnotationArbitrationPage(value),
              projectId,
              "标注仲裁列表",
            ),
        },
      );
    },

    async createArbitrationForSubmission(
      projectId,
      submissionId,
      input,
      options,
    ) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/arbitrations`,
        ),
        {
          method: "POST",
          signal: options?.signal,
          headers: createHeaders(options),
          body: {
            arbiter_ids: input.arbiterIds,
            quorum: input.quorum,
            trigger: input.trigger,
            review_id: input.reviewId ?? null,
          },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationArbitration(value),
              projectId,
              "标注仲裁",
            ),
        },
      );
    },

    async createArbitration(projectId, input, options) {
      return client.request(
        projectPath(projectId, "/annotation-arbitrations"),
        {
          method: "POST",
          signal: options?.signal,
          headers: createHeaders(options),
          body: {
            submission_id: input.submissionId,
            arbiter_ids: input.arbiterIds,
            quorum: input.quorum,
            trigger: input.trigger,
            review_id: input.reviewId ?? null,
          },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationArbitration(value),
              projectId,
              "标注仲裁",
            ),
        },
      );
    },

    async getArbitration(projectId, arbitrationId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-arbitrations/${requireIdentifier(arbitrationId, "arbitration_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationArbitration(value),
              projectId,
              "标注仲裁",
            ),
        },
      );
    },

    async actOnArbitration(projectId, arbitrationId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-arbitrations/${requireIdentifier(arbitrationId, "arbitration_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationArbitration(value),
              projectId,
              "标注仲裁",
            ),
        },
      );
    },

    async voteOnArbitration(projectId, arbitrationId, input, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-arbitrations/${requireIdentifier(arbitrationId, "arbitration_id")}/votes`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { decision: input.decision, reason: input.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationArbitration(value),
              projectId,
              "标注仲裁",
            ),
        },
      );
    },
  };
}
