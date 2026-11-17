/**
 * 标注质量接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/gold-sets`、`/quality-samples`、`/iaa-reports` 三类质量资源，
 * 以及 N4 的提交质量检查（`/annotation-submissions/{id}/quality-checks`）。
 * 黄金集答案与详情同路径族但响应是授权答案视图，仍按黄金集解析器校验。
 */
import type { CursorPage } from "../types/api";
import type { GoldSet, IAAReport, QualitySample } from "../types/annotation";
import type {
  GoldSetCreateInput,
  IAAReportCreateInput,
  QualityCheckRun,
} from "../types/quality";
import type { WithAllowedActions } from "./annotationAllowedActions";
import type { ApiClient } from "./api";
import {
  parseGoldSet,
  parseGoldSetPage,
  parseIAAReport,
  parseIAAReportPage,
  parseQualitySample,
  parseQualitySamplePage,
} from "./annotationResultParsers";
import {
  createHeaders,
  writeHeaders,
  type AnnotationCreateOptions,
  type AnnotationListOptions,
  type AnnotationWriteOptions,
  type DetailRequestOptions,
} from "./annotationRequestBase";
import { parseQualityCheckRun, parseQualityCheckRunPage } from "./qualityCheckParsers";
import {
  assertPageScope,
  assertProjectScope,
  MAX_PAGE_LIMIT,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";
import { ContractValidationError } from "./validation";

/** 按计划抽样的候选提交；`risk_score` 只在 RISK_BASED 策略下参与排序。 */
export interface QualitySamplePlanCandidate {
  submission_id: string;
  stratum?: string | null;
  risk_score?: number;
}

/** 按计划批量生成抽检（后端 `QualitySamplePlanRequest`）。 */
export interface QualitySamplePlanInput {
  task_id: string;
  candidates: QualitySamplePlanCandidate[];
  sampling_policy_version: string;
  sample_type: string;
  sample_size: number;
  sample_reason: string;
  stratum?: string | null;
  risk_threshold?: number | null;
  due_at?: string | null;
}

/** 手工创建单条抽检（后端 `QualitySampleCreate`）。 */
export interface QualitySampleCreateInput {
  task_id: string;
  submission_id: string;
  annotator_id: string;
  sampling_policy_version: string;
  sample_type: string;
  sample_reason: string;
  item_refs?: string[];
  stratum?: string | null;
  due_at?: string | null;
  metadata?: Record<string, unknown>;
}

/** 页面一次性提交「按计划生成」或「手工创建」两种模式。 */
export type QualitySampleCreateRequest =
  | { mode: "plan"; input: QualitySamplePlanInput }
  | { mode: "manual"; input: QualitySampleCreateInput };

/** 抽检动作：`assign` 需要 reviewer_id，失败/升级/关闭需要原因。 */
export interface QualitySampleActionOptions extends AnnotationWriteOptions {
  reviewerId?: string | null;
}

export interface AnnotationQualityApi {
  listGoldSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<WithAllowedActions<GoldSet>>>;
  getGoldSet: (
    projectId: string,
    goldSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<GoldSet>>;
  getGoldSetAnswers: (
    projectId: string,
    goldSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<GoldSet>>;
  listQualitySamples: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions & { status?: string },
  ) => Promise<CursorPage<WithAllowedActions<QualitySample>>>;
  getQualitySample: (
    projectId: string,
    qualitySampleId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<QualitySample>>;
  listIAAReports: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<IAAReport>>;
  getIAAReport: (
    projectId: string,
    reportId: string,
    options?: DetailRequestOptions,
  ) => Promise<IAAReport>;
  /** 创建黄金集（初始 DRAFT）；创建只需幂等键，没有 If-Match。 */
  createGoldSet: (
    projectId: string,
    input: GoldSetCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<GoldSet>>;
  /** 黄金集状态动作；契约要求 `If-Match` + `Idempotency-Key`。 */
  actOnGoldSet: (
    projectId: string,
    goldSetId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<WithAllowedActions<GoldSet>>;
  /** 创建 IAA 计算（同步返回；样本不足时 status=INSUFFICIENT_DATA）。 */
  createIAAReport: (
    projectId: string,
    input: IAAReportCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<IAAReport>;
  /** 按计划批量生成抽检；返回本次生成的样本（可能少于候选数）。 */
  planQualitySamples: (
    projectId: string,
    input: QualitySamplePlanInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<QualitySample>[]>;
  /** 手工创建单条抽检。 */
  createQualitySample: (
    projectId: string,
    input: QualitySampleCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<QualitySample>>;
  /** 抽检状态动作；契约要求 `If-Match` + `Idempotency-Key`。 */
  actOnQualitySample: (
    projectId: string,
    qualitySampleId: string,
    action: string,
    options: QualitySampleActionOptions,
  ) => Promise<WithAllowedActions<QualitySample>>;
  /** 某提交的质量检查历史（只追加，按 run_id 排序）。 */
  listQualityChecks: (
    projectId: string,
    submissionId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<QualityCheckRun>>;
  /**
   * 对提交手动重跑一次质量检查。
   *
   * 契约语义：每次调用都产生**新的 run**（提交时的自动检查是首次执行）。
   * 因此这里每次都发新幂等键，避免把"内容变更后重跑"复放到上一次结果上。
   */
  runQualityCheck: (
    projectId: string,
    submissionId: string,
    options?: AnnotationCreateOptions,
  ) => Promise<QualityCheckRun>;
}

export function createAnnotationQualityApi(
  client: ApiClient,
): AnnotationQualityApi {
  return {
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
          cursor: options?.cursor,
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
          cursor: options?.cursor,
          limit: options?.limit,
        },
        validate: (value) =>
          assertPageScope(parseIAAReportPage(value), projectId, "IAA 报告列表"),
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

    async createGoldSet(projectId, input, options) {
      return client.request(projectPath(projectId, "/gold-sets"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseGoldSet(value), projectId, "黄金集"),
      });
    },

    async actOnGoldSet(projectId, goldSetId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/gold-sets/${requireIdentifier(goldSetId, "gold_set_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          // GoldSetActionRequest.reason 可选；显式 null 与「未提交」服务端语义
          // 一致（都视为未填），这里统一提交以便审计链路有确定字段。
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseGoldSet(value), projectId, "黄金集"),
        },
      );
    },

    async createIAAReport(projectId, input, options) {
      return client.request(projectPath(projectId, "/iaa-reports"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseIAAReport(value), projectId, "IAA 报告"),
      });
    },

    async planQualitySamples(projectId, input, options) {
      return client.request(projectPath(projectId, "/quality-samples/plan"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => {
          if (!Array.isArray(value)) {
            throw new ContractValidationError("抽检计划响应必须是数组");
          }
          return value.map((item, index) =>
            assertProjectScope(
              parseQualitySample(item, `抽检计划[${index}]`),
              projectId,
              "质检抽检",
            ),
          );
        },
      });
    },

    async createQualitySample(projectId, input, options) {
      return client.request(projectPath(projectId, "/quality-samples"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseQualitySample(value), projectId, "质检抽检"),
      });
    },

    async actOnQualitySample(projectId, qualitySampleId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/quality-samples/${requireIdentifier(qualitySampleId, "quality_sample_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {
            reviewer_id: options.reviewerId ?? null,
            reason: options.reason ?? null,
          },
          validate: (value) =>
            assertProjectScope(parseQualitySample(value), projectId, "质检抽检"),
        },
      );
    },

    async listQualityChecks(projectId, submissionId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/quality-checks`,
        ),
        {
          signal: options?.signal,
          query: {
            cursor: options?.cursor,
            limit: options?.limit ?? MAX_PAGE_LIMIT,
          },
          validate: (value) =>
            assertPageScope(
              parseQualityCheckRunPage(value),
              projectId,
              "质量检查历史",
            ),
        },
      );
    },

    async runQualityCheck(projectId, submissionId, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-submissions/${requireIdentifier(submissionId, "submission_id")}/quality-checks`,
        ),
        {
          method: "POST",
          signal: options?.signal,
          // 契约 `paths.stage8.yaml#/submissionQualityChecks` 声明了
          // Idempotency-Key 参数；后端把每次检查视为只追加的新记录，
          // 因此每次点击都生成新键，不会把重跑复放到上一次结论。
          headers: createHeaders(options),
          validate: (value) =>
            assertProjectScope(
              parseQualityCheckRun(value),
              projectId,
              "质量检查",
            ),
        },
      );
    },
  };
}
