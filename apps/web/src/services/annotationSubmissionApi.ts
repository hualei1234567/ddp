/**
 * 标注提交、记录与复核接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-submissions`（含 records、reviews 子资源）与
 * `/annotation-reviews`。复核挂在提交下和独立创建是后端两个入口，
 * 这里同时保留，调用方按场景选择。
 */
import type { CursorPage } from "../types/api";
import type {
  AnnotationRecord,
  AnnotationReview,
  AnnotationSubmission,
  AnnotationSubmissionCreateInput,
  DataSource,
} from "../types/annotation";
import type { ApiClient } from "./api";
import {
  parseAnnotationRecordPage,
  parseAnnotationReview,
  parseAnnotationReviewPage,
  parseAnnotationSubmission,
  parseAnnotationSubmissionPage,
} from "./annotationResultParsers";
import {
  createHeaders,
  writeHeaders,
  type AnnotationCreateOptions,
  type AnnotationWriteOptions,
  type DetailRequestOptions,
  type ReviewActionOptions,
  type ReviewCreateInput,
  type SubmissionCreateOptions,
  type SubmissionListOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  projectPath,
  requireIdentifier,
} from "./projectScope";

export interface AnnotationSubmissionApi {
  listSubmissions: (
    projectId: string,
    taskId: string,
    options?: SubmissionListOptions,
  ) => Promise<CursorPage<AnnotationSubmission>>;
  getSubmission: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationSubmission>;
  listSubmissionRecords: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<CursorPage<AnnotationRecord>>;
  createSubmission: (
    projectId: string,
    input: {
      assignment_id: string;
      records: AnnotationSubmissionCreateInput["records"];
      data_source: DataSource;
    },
    options?: SubmissionCreateOptions,
  ) => Promise<AnnotationSubmission>;
  actOnSubmission: (
    projectId: string,
    submissionId: string,
    action: "submit" | "withdraw",
    options: AnnotationWriteOptions,
  ) => Promise<AnnotationSubmission>;
  listSubmissionReviews: (
    projectId: string,
    submissionId: string,
    options?: DetailRequestOptions,
  ) => Promise<CursorPage<AnnotationReview>>;
  createReviewForSubmission: (
    projectId: string,
    submissionId: string,
    reviewerId: string,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationReview>;
  createReview: (
    projectId: string,
    input: ReviewCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationReview>;
  getReview: (
    projectId: string,
    reviewId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationReview>;
  actOnReview: (
    projectId: string,
    reviewId: string,
    action: string,
    options: ReviewActionOptions,
  ) => Promise<AnnotationReview>;
}

export function createAnnotationSubmissionApi(
  client: ApiClient,
): AnnotationSubmissionApi {
  return {
    async listSubmissions(projectId, taskId, options) {
      return client.get(projectPath(projectId, "/annotation-submissions"), {
        signal: options?.signal,
        query: {
          task_id: requireIdentifier(taskId, "task_id"),
          status: options?.status,
        },
        validate: (value) =>
          assertPageScope(
            parseAnnotationSubmissionPage(value),
            projectId,
            "标注提交列表",
          ),
      });
    },

    async getSubmission(projectId, submissionId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationSubmission(value),
              projectId,
              "标注提交",
            ),
        },
      );
    },

    async listSubmissionRecords(projectId, submissionId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/records`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertPageScope(
              parseAnnotationRecordPage(value),
              projectId,
              "标注记录列表",
            ),
        },
      );
    },

    async createSubmission(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-submissions"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: {
          assignment_id: input.assignment_id,
          records: input.records,
          data_source: input.data_source,
        },
        validate: (value) =>
          assertProjectScope(
            parseAnnotationSubmission(value),
            projectId,
            "标注提交",
          ),
      });
    },

    async actOnSubmission(projectId, submissionId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationSubmission(value),
              projectId,
              "标注提交",
            ),
        },
      );
    },

    async listSubmissionReviews(projectId, submissionId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/reviews`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertPageScope(
              parseAnnotationReviewPage(value),
              projectId,
              "标注复核列表",
            ),
        },
      );
    },

    async createReviewForSubmission(
      projectId,
      submissionId,
      reviewerId,
      options,
    ) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/reviews`,
        ),
        {
          method: "POST",
          signal: options?.signal,
          headers: createHeaders(options),
          body: { reviewer_id: requireIdentifier(reviewerId, "reviewer_id") },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationReview(value),
              projectId,
              "标注复核",
            ),
        },
      );
    },

    async createReview(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-reviews"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: {
          submission_id: input.submissionId,
          reviewer_id: input.reviewerId,
        },
        validate: (value) =>
          assertProjectScope(
            parseAnnotationReview(value),
            projectId,
            "标注复核",
          ),
      });
    },

    async getReview(projectId, reviewId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-reviews/${requireIdentifier(reviewId, "review_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationReview(value),
              projectId,
              "标注复核",
            ),
        },
      );
    },

    async actOnReview(projectId, reviewId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-reviews/${requireIdentifier(reviewId, "review_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {
            reason: options.reason ?? null,
            issues: options.issues ?? null,
            checklist: options.checklist ?? null,
            review_duration_sec: options.reviewDurationSec ?? null,
          },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationReview(value),
              projectId,
              "标注复核",
            ),
        },
      );
    },
  };
}
