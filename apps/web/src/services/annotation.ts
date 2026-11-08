import type { CursorPage } from "../types/api";
import type {
  AnnotationArbitration,
  AnnotationAssignment,
  AnnotationBatch,
  AnnotationRecord,
  AnnotationReview,
  AnnotationSampleSet,
  AnnotationSchema,
  AnnotationSubmission,
  AnnotationSubmissionCreateInput,
  AnnotationTask,
  AnnotationTaskCreateInput,
  ArbitrationDecision,
  DataSource,
  GoldSet,
  IAAReport,
  QualitySample,
  ReviewChecklistItem,
  ReviewIssue,
} from "../types/annotation";
import { apiClient, type ApiClient } from "./api";
import {
  parseAnnotationAssignment,
  parseAnnotationAssignmentPage,
  parseAnnotationBatch,
  parseAnnotationBatchPage,
  parseAnnotationSampleSet,
  parseAnnotationSampleSetPage,
  parseAnnotationSchema,
  parseAnnotationSchemaPage,
  parseAnnotationTask,
  parseAnnotationTaskPage,
} from "./annotationParsers";
import {
  parseAnnotationArbitration,
  parseAnnotationArbitrationPage,
  parseAnnotationRecordPage,
  parseAnnotationReview,
  parseAnnotationReviewPage,
  parseAnnotationSubmission,
  parseAnnotationSubmissionPage,
  parseGoldSet,
  parseGoldSetPage,
  parseIAAReport,
  parseIAAReportPage,
  parseQualitySample,
  parseQualitySamplePage,
} from "./annotationResultParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface AnnotationListOptions extends PageRequestOptions {
  status?: string;
}

export interface AnnotationWriteOptions {
  /** 服务端当前 version；写动作必须带 If-Match。 */
  version: number;
  reason?: string | null;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface AnnotationCreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface AssignmentListOptions extends PageRequestOptions {
  taskId?: string;
  batchId?: string;
  assigneeId?: string;
  status?: string;
}

export interface SubmissionListOptions {
  status?: string;
  signal?: AbortSignal;
}

export type SubmissionCreateOptions = AnnotationCreateOptions;

export interface ReviewActionOptions extends AnnotationWriteOptions {
  issues?: ReviewIssue[];
  checklist?: ReviewChecklistItem[];
  reviewDurationSec?: number;
}

export interface ArbitrationCreateInput {
  submissionId: string;
  arbiterIds: string[];
  quorum: number;
  trigger: string;
  reviewId?: string | null;
}

export interface ReviewCreateInput {
  submissionId: string;
  reviewerId: string;
}

export interface AnnotationApi {
  listSchemas: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationSchema>>;
  getSchema: (
    projectId: string,
    schemaId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationSchema>;
  listSampleSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationSampleSet>>;
  getSampleSet: (
    projectId: string,
    sampleSetId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationSampleSet>;
  listTasks: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationTask>>;
  createTask: (
    projectId: string,
    input: AnnotationTaskCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationTask>;
  getTask: (
    projectId: string,
    taskId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationTask>;
  actOnTask: (
    projectId: string,
    taskId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<AnnotationTask>;
  listBatches: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<AnnotationBatch>>;
  getBatch: (
    projectId: string,
    batchId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationBatch>;
  listAssignments: (
    projectId: string,
    options?: AssignmentListOptions,
  ) => Promise<CursorPage<AnnotationAssignment>>;
  getAssignment: (
    projectId: string,
    assignmentId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationAssignment>;
  listSubmissions: (
    projectId: string,
    taskId: string,
    options?: SubmissionListOptions,
  ) => Promise<CursorPage<AnnotationSubmission>>;
  getSubmission: (
    projectId: string,
    submissionId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationSubmission>;
  listSubmissionRecords: (
    projectId: string,
    submissionId: string,
    options?: Pick<PageRequestOptions, "signal">,
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
    options?: Pick<PageRequestOptions, "signal">,
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
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<AnnotationReview>;
  actOnReview: (
    projectId: string,
    reviewId: string,
    action: string,
    options: ReviewActionOptions,
  ) => Promise<AnnotationReview>;
  listSubmissionArbitrations: (
    projectId: string,
    submissionId: string,
    options?: Pick<PageRequestOptions, "signal">,
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
    options?: Pick<PageRequestOptions, "signal">,
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
  listGoldSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<GoldSet>>;
  getGoldSet: (
    projectId: string,
    goldSetId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<GoldSet>;
  getGoldSetAnswers: (
    projectId: string,
    goldSetId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<GoldSet>;
  listQualitySamples: (
    projectId: string,
    taskId: string,
    options?: { status?: string; limit?: number; signal?: AbortSignal },
  ) => Promise<CursorPage<QualitySample>>;
  getQualitySample: (
    projectId: string,
    qualitySampleId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<QualitySample>;
  listIAAReports: (
    projectId: string,
    taskId: string,
    options?: { limit?: number; signal?: AbortSignal },
  ) => Promise<CursorPage<IAAReport>>;
  getIAAReport: (
    projectId: string,
    reportId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<IAAReport>;
}

/**
 * 阶段 3 标注 API。
 *
 * 路径来自后端实际路由；所有响应先严格解析，再校验 `project_id` 与请求
 * 路径一致。权限、职责分离与状态迁移仍以服务端为准。
 */
export function createAnnotationApi(client: ApiClient): AnnotationApi {
  const writeHeaders = (options: AnnotationWriteOptions) => ({
    "If-Match": formatIfMatch(options.version),
    "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
  });
  const createHeaders = (options?: AnnotationCreateOptions) => ({
    "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey(),
  });

  return {
    async listSchemas(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-schemas"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            parseAnnotationSchemaPage(value),
            projectId,
            "标注 Schema 列表",
          ),
      });
    },

    async getSchema(projectId, schemaId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-schemas/${requireIdentifier(schemaId, "schema_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseAnnotationSchema(value), projectId, "标注 Schema"),
        },
      );
    },

    async listSampleSets(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-sample-sets"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            parseAnnotationSampleSetPage(value),
            projectId,
            "标注样本集列表",
          ),
      });
    },

    async getSampleSet(projectId, sampleSetId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-sample-sets/${requireIdentifier(sampleSetId, "sample_set_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationSampleSet(value),
              projectId,
              "标注样本集",
            ),
        },
      );
    },

    async listTasks(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-tasks"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            parseAnnotationTaskPage(value),
            projectId,
            "标注任务列表",
          ),
      });
    },

    async createTask(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-tasks"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseAnnotationTask(value), projectId, "标注任务"),
      });
    },

    async getTask(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseAnnotationTask(value), projectId, "标注任务"),
        },
      );
    },

    async actOnTask(projectId, taskId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseAnnotationTask(value), projectId, "标注任务"),
        },
      );
    },

    async listBatches(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}/batches`,
        ),
        {
          signal: options?.signal,
          query: pageQuery(options),
          validate: (value) =>
            assertPageScope(
              parseAnnotationBatchPage(value),
              projectId,
              "标注批次列表",
            ),
        },
      );
    },

    async getBatch(projectId, batchId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-batches/${requireIdentifier(batchId, "batch_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseAnnotationBatch(value), projectId, "标注批次"),
        },
      );
    },

    async listAssignments(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-assignments"), {
        signal: options?.signal,
        query: {
          task_id: options?.taskId,
          batch_id: options?.batchId,
          assignee_id: options?.assigneeId,
          status: options?.status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            parseAnnotationAssignmentPage(value),
            projectId,
            "标注分配列表",
          ),
      });
    },

    async getAssignment(projectId, assignmentId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-assignments/${requireIdentifier(assignmentId, "assignment_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationAssignment(value),
              projectId,
              "标注分配",
            ),
        },
      );
    },

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

    async createReviewForSubmission(projectId, submissionId, reviewerId, options) {
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
          assertProjectScope(parseAnnotationReview(value), projectId, "标注复核"),
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
      return client.request(projectPath(projectId, "/annotation-arbitrations"), {
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
      });
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

    async listGoldSets(projectId, options) {
      return client.get(projectPath(projectId, "/gold-sets"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseGoldSetPage(value), projectId, "黄金集列表"),
      });
    },

    async getGoldSet(projectId, goldSetId, options) {
      return client.get(
        projectPath(
          projectId,
          `/gold-sets/${requireIdentifier(goldSetId, "gold_set_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseGoldSet(value), projectId, "黄金集"),
        },
      );
    },

    async getGoldSetAnswers(projectId, goldSetId, options) {
      return client.get(
        projectPath(
          projectId,
          `/gold-sets/${requireIdentifier(goldSetId, "gold_set_id")}/answers`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseGoldSet(value), projectId, "黄金集答案"),
        },
      );
    },

    async listQualitySamples(projectId, taskId, options) {
      return client.get(projectPath(projectId, "/quality-samples"), {
        signal: options?.signal,
        query: {
          task_id: requireIdentifier(taskId, "task_id"),
          status: options?.status,
          limit: options?.limit,
        },
        validate: (value) =>
          assertPageScope(
            parseQualitySamplePage(value),
            projectId,
            "质检抽检列表",
          ),
      });
    },

    async getQualitySample(projectId, qualitySampleId, options) {
      return client.get(
        projectPath(
          projectId,
          `/quality-samples/${requireIdentifier(qualitySampleId, "quality_sample_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseQualitySample(value),
              projectId,
              "质检抽检",
            ),
        },
      );
    },

    async listIAAReports(projectId, taskId, options) {
      return client.get(projectPath(projectId, "/iaa-reports"), {
        signal: options?.signal,
        query: {
          task_id: requireIdentifier(taskId, "task_id"),
          limit: options?.limit,
        },
        validate: (value) =>
          assertPageScope(
            parseIAAReportPage(value),
            projectId,
            "IAA 报告列表",
          ),
      });
    },

    async getIAAReport(projectId, reportId, options) {
      return client.get(
        projectPath(
          projectId,
          `/iaa-reports/${requireIdentifier(reportId, "report_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseIAAReport(value), projectId, "IAA 报告"),
        },
      );
    },
  };
}

export const annotationApi = createAnnotationApi(apiClient);
