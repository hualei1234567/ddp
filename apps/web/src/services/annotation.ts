/**
 * 阶段 3 标注 API 的组合入口（由原 916 行单文件拆分，见 D-003 / ADR-0030）。
 *
 * 职责：只做组合——把 schema / task / submission / arbitration / quality /
 * gold-set / ai-suggestions 七个资源域的工厂合并成一个 `AnnotationApi`，
 * 并导出单例。
 * 选项类型与写请求头在 `annotationRequestBase.ts`，各域实现见同目录 `*Api.ts`。
 * 外部调用点（页面、hooks、集成测试）的导入路径保持不变。
 *
 * 路径来自后端实际路由；所有响应先严格解析，再校验 `project_id` 与请求
 * 路径一致。权限、职责分离与状态迁移仍以服务端为准。
 */
import { apiClient, type ApiClient } from "./api";
import {
  createAnnotationAiApi,
  type AnnotationAiApi,
} from "./annotationAiApi";
import {
  createAnnotationArbitrationApi,
  type AnnotationArbitrationApi,
} from "./annotationArbitrationApi";
import {
  createAnnotationGoldSetApi,
  type AnnotationGoldSetApi,
} from "./annotationGoldSetApi";
import {
  createAnnotationQualityApi,
  type AnnotationQualityApi,
} from "./annotationQualityApi";
import {
  createAnnotationSchemaApi,
  type AnnotationSchemaApi,
} from "./annotationSchemaApi";
import {
  createAnnotationSubmissionApi,
  type AnnotationSubmissionApi,
} from "./annotationSubmissionApi";
import {
  createAnnotationTaskApi,
  type AnnotationTaskApi,
} from "./annotationTaskApi";

export type {
  AnnotationSchemaCreateInput,
  AnnotationSampleSetActionOptions,
  AnnotationSampleSetCreateInput,
  AnnotationSchemaPatchInput,
} from "./annotationSchemaApi";

export type {
  AnnotationAssignmentActionOptions,
  AnnotationAssignmentCreateInput,
  AnnotationBatchCreateInput,
  WorkloadQuery,
} from "./annotationTaskApi";

export type {
  QualitySampleActionOptions,
  QualitySampleCreateInput,
  QualitySamplePlanCandidate,
  QualitySamplePlanInput,
} from "./annotationQualityApi";

export type {
  GoldEvaluationCreateInput,
  GoldInjectionCreateInput,
} from "./annotationGoldSetApi";

export type {
  AiSuggestion,
  SuggestionAgreement,
  SuggestionAgreementItem,
} from "./annotationAiParsers";

export type { GoldEvaluation, GoldInjection } from "./annotationQualityParsers";

export type {
  AnnotationActionKind,
  AnnotationActionResolution,
} from "./annotationActions";

export type {
  AnnotationCreateOptions,
  AnnotationListOptions,
  AnnotationWriteOptions,
  ArbitrationCreateInput,
  AssignmentListOptions,
  ReviewActionOptions,
  ReviewCreateInput,
  SubmissionCreateOptions,
  SubmissionListOptions,
} from "./annotationRequestBase";

/**
 * 标注 API：按资源域组合，方法名与签名与原单文件保持兼容，
 * 本轮补齐 Schema / 样本集 / 批次 / 分配 / 抽检 / 黄金集 / AI 建议写入口。
 */
export interface AnnotationApi
  extends AnnotationSchemaApi,
    AnnotationTaskApi,
    AnnotationSubmissionApi,
    AnnotationArbitrationApi,
    AnnotationQualityApi,
    AnnotationGoldSetApi,
    AnnotationAiApi {}

export function createAnnotationApi(client: ApiClient): AnnotationApi {
  return {
    ...createAnnotationSchemaApi(client),
    ...createAnnotationTaskApi(client),
    ...createAnnotationSubmissionApi(client),
    ...createAnnotationArbitrationApi(client),
    ...createAnnotationQualityApi(client),
    ...createAnnotationGoldSetApi(client),
    ...createAnnotationAiApi(client),
  };
}

export const annotationApi = createAnnotationApi(apiClient);
