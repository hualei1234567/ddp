/**
 * 标注质量接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/gold-sets`、`/quality-samples`、`/iaa-reports` 三类质量资源。
 * 黄金集答案与详情同路径族但响应是授权答案视图，仍按黄金集解析器校验。
 */
import type { CursorPage } from "../types/api";
import type { GoldSet, IAAReport, QualitySample } from "../types/annotation";
import type { ApiClient } from "./api";
import {
  parseGoldSet,
  parseGoldSetPage,
  parseIAAReport,
  parseIAAReportPage,
  parseQualitySample,
  parseQualitySamplePage,
} from "./annotationResultParsers";
import type {
  AnnotationListOptions,
  DetailRequestOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
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
    options?: { status?: string; limit?: number; signal?: AbortSignal },
  ) => Promise<CursorPage<QualitySample>>;
  getQualitySample: (
    projectId: string,
    qualitySampleId: string,
    options?: DetailRequestOptions,
  ) => Promise<QualitySample>;
  listIAAReports: (
    projectId: string,
    taskId: string,
    options?: { limit?: number; signal?: AbortSignal },
  ) => Promise<CursorPage<IAAReport>>;
  getIAAReport: (
    projectId: string,
    reportId: string,
    options?: DetailRequestOptions,
  ) => Promise<IAAReport>;
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
  };
}
