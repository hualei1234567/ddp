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

export interface AnnotationQualityApi {
  listGoldSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<GoldSet>>;
  getGoldSet: (
    projectId: string,
    goldSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<GoldSet>;
  getGoldSetAnswers: (
    projectId: string,
    goldSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<GoldSet>;
  listQualitySamples: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions & { status?: string },
  ) => Promise<CursorPage<QualitySample>>;
  getQualitySample: (
    projectId: string,
    qualitySampleId: string,
    options?: DetailRequestOptions,
  ) => Promise<QualitySample>;
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
  ) => Promise<GoldSet>;
  /** 黄金集状态动作；契约要求 `If-Match` + `Idempotency-Key`。 */
  actOnGoldSet: (
    projectId: string,
    goldSetId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<GoldSet>;
  /** 创建 IAA 计算（同步返回；样本不足时 status=INSUFFICIENT_DATA）。 */
  createIAAReport: (
    projectId: string,
    input: IAAReportCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<IAAReport>;
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
