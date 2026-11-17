/** 阶段 5 模型闭环 API：模型、评测、推理、反馈、缺口与数据需求。 */

import type { CursorPage } from "../types/api";
import type {
  DataRequirement,
  Evaluation,
  GapDimension,
  GapSummary,
  InferenceService,
  InferenceTask,
  Model,
  ModelVersion,
} from "../types/modelops";
import { apiClient, type ApiClient } from "./api";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  parseEvaluation,
  parseEvaluationPage,
  parseFeedback,
  parseFeedbackPage,
  parseGapSummary,
  parseInferenceService,
  parseInferenceServicePage,
  parseInferenceTask,
  parseInferenceTaskPage,
  parseModel,
  parseModelPage,
  parseModelVersion,
  parseModelVersionPage,
  parseRequirement,
  parseRequirementPage,
} from "./modelopsParsers";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface WriteOptions {
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ModelCreateInput {
  code: string;
  name: string;
  task_type: string;
  framework: string;
  owner_id: string;
  description?: string | null;
  tags?: string[];
}

export type ModelPatchInput = Partial<
  Pick<ModelCreateInput, "name" | "description" | "framework" | "tags">
>;

export interface ArtifactInput {
  object_key: string;
  sha256: string;
  size_bytes: number;
  format: string;
}

export interface RuntimeInput {
  framework: string;
  framework_version: string;
  python_version?: string | null;
  cuda_version?: string | null;
  image_ref?: string | null;
}

export interface ModelVersionCreateInput {
  artifact: ArtifactInput;
  runtime: RuntimeInput;
  training_request_id?: string | null;
  training_delivery_id?: string | null;
  external_ref_id?: string | null;
  dataset_version_ids?: string[];
  metrics_summary?: Record<string, number>;
  release_notes?: string | null;
}

export interface EvaluationCreateInput {
  model_version_id: string;
  dataset_version_id: string;
  metrics?: Record<string, number>;
  thresholds: Record<string, { min?: number | null; max?: number | null }>;
  baseline_evaluation_id?: string | null;
  adapter_id?: string | null;
  /**
   * 适配器执行参数，原样透传。多产物项目必须给 `model_object_key`，
   * 否则适配器无法安全选择要评测的是哪个产物（会明确报 ambiguous）。
   */
  config?: Record<string, unknown>;
}

export interface InferenceServiceCreateInput {
  model_version_id: string;
  environment: string;
  endpoint_url: string;
  resource_ref?: string | null;
  config?: Record<string, unknown>;
}

export interface InferenceTaskCreateInput {
  model_version_id: string;
  service_id?: string | null;
  dataset_version_id?: string | null;
  episode_ids?: string[];
  adapter_id?: string | null;
  config?: Record<string, unknown>;
}

export interface FeedbackCreateInput {
  model_version_id: string;
  evaluation_id?: string | null;
  inference_task_id?: string | null;
  issue_type: string;
  severity: string;
  description: string;
  dimensions: GapDimension[];
  evidence_refs?: string[];
}

export interface RequirementCreateInput {
  code: string;
  title: string;
  source_type: string;
  dimensions: GapDimension[];
  owner_id: string;
  description?: string | null;
  source_feedback_ids?: string[];
  source_evaluation_ids?: string[];
  target_episode_count?: number;
  quality_thresholds?: Record<string, unknown>;
  priority?: string;
  due_at?: string | null;
}

export interface FeedbackActionInput {
  reason?: string | null;
  resolution_note?: string | null;
  requirement_code?: string | null;
  requirement_title?: string | null;
  target_episode_count?: number | null;
  due_at?: string | null;
  priority?: string | null;
}

export interface RequirementActionInput {
  reason?: string | null;
  dataset_version_ids?: string[];
  evaluation_id?: string | null;
  satisfaction_evidence_ref?: string | null;
}

function createHeaders(options?: CreateOptions): Record<string, string> {
  return { "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey() };
}

function writeHeaders(options: WriteOptions): Record<string, string> {
  return {
    "If-Match": formatIfMatch(options.version),
    "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
  };
}

/** 动作请求体只下发契约字段；version/Idempotency-Key/signal 不能进入 JSON。 */
const RESERVED_ACTION_KEYS = new Set(["version", "idempotencyKey", "signal"]);

function toActionBody<T extends object>(options: T): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(options).filter(([key]) => !RESERVED_ACTION_KEYS.has(key)),
  );
}

function modelPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/models${suffix}`);
}

function evaluationPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/evaluations${suffix}`);
}

function feedbackPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/model-feedback${suffix}`);
}

function requirementPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/data-requirements${suffix}`);
}

function inferencePath(projectId: string, suffix = ""): string {
  return projectPath(projectId, suffix);
}

export interface ModelOpsApi {
  listModels(
    projectId: string,
    options?: PageRequestOptions & { status?: string; task_type?: string },
  ): Promise<CursorPage<Model>>;
  createModel(
    projectId: string,
    input: ModelCreateInput,
    options?: CreateOptions,
  ): Promise<Model>;
  getModel(
    projectId: string,
    modelId: string,
    options?: { signal?: AbortSignal },
  ): Promise<Model>;
  updateModel(
    projectId: string,
    modelId: string,
    input: ModelPatchInput,
    options: WriteOptions,
  ): Promise<Model>;
  actModel(
    projectId: string,
    modelId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<Model>;
  listVersions(
    projectId: string,
    modelId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<ModelVersion>>;
  createVersion(
    projectId: string,
    modelId: string,
    input: ModelVersionCreateInput,
    options?: CreateOptions,
  ): Promise<ModelVersion>;
  getVersion(
    projectId: string,
    modelId: string,
    versionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ModelVersion>;
  actVersion(
    projectId: string,
    modelId: string,
    versionId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<ModelVersion>;
  listEvaluations(
    projectId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<Evaluation>>;
  createEvaluation(
    projectId: string,
    input: EvaluationCreateInput,
    options?: CreateOptions,
  ): Promise<Evaluation>;
  getEvaluation(
    projectId: string,
    evaluationId: string,
    options?: { signal?: AbortSignal },
  ): Promise<Evaluation>;
  actEvaluation(
    projectId: string,
    evaluationId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<Evaluation>;
  listServices(
    projectId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<InferenceService>>;
  createService(
    projectId: string,
    input: InferenceServiceCreateInput,
    options?: CreateOptions,
  ): Promise<InferenceService>;
  getService(
    projectId: string,
    serviceId: string,
    options?: { signal?: AbortSignal },
  ): Promise<InferenceService>;
  actService(
    projectId: string,
    serviceId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<InferenceService>;
  listTasks(
    projectId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<InferenceTask>>;
  createTask(
    projectId: string,
    input: InferenceTaskCreateInput,
    options?: CreateOptions,
  ): Promise<InferenceTask>;
  getTask(
    projectId: string,
    taskId: string,
    options?: { signal?: AbortSignal },
  ): Promise<InferenceTask>;
  actTask(
    projectId: string,
    taskId: string,
    action: string,
    options: WriteOptions & { reason?: string | null },
  ): Promise<InferenceTask>;
  listFeedback(
    projectId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<import("../types/modelops").ModelFeedback>>;
  createFeedback(
    projectId: string,
    input: FeedbackCreateInput,
    options?: CreateOptions,
  ): Promise<import("../types/modelops").ModelFeedback>;
  getFeedback(
    projectId: string,
    feedbackId: string,
    options?: { signal?: AbortSignal },
  ): Promise<import("../types/modelops").ModelFeedback>;
  actFeedback(
    projectId: string,
    feedbackId: string,
    action: string,
    options: WriteOptions & FeedbackActionInput,
  ): Promise<import("../types/modelops").ModelFeedback>;
  getDataGaps(
    projectId: string,
    options?: { signal?: AbortSignal; dimension_type?: string },
  ): Promise<GapSummary>;
  listRequirements(
    projectId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<DataRequirement>>;
  createRequirement(
    projectId: string,
    input: RequirementCreateInput,
    options?: CreateOptions,
  ): Promise<DataRequirement>;
  getRequirement(
    projectId: string,
    requirementId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DataRequirement>;
  actRequirement(
    projectId: string,
    requirementId: string,
    action: string,
    options: WriteOptions & RequirementActionInput,
  ): Promise<DataRequirement>;
}

export function createModelOpsApi(client: ApiClient): ModelOpsApi {
  return {
    async listModels(projectId, options) {
      return client.get(modelPath(projectId), {
        signal: options?.signal,
        query: {
          status: options?.status,
          task_type: options?.task_type,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseModelPage(value), projectId, "模型列表"),
      });
    },
    async createModel(projectId, input, options) {
      return client.request(modelPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseModel(value), projectId, "模型"),
      });
    },
    async getModel(projectId, modelId, options) {
      return client.get(modelPath(projectId, `/${requireIdentifier(modelId, "model_id")}`), {
        signal: options?.signal,
        validate: (value) => assertProjectScope(parseModel(value), projectId, "模型"),
      });
    },
    async updateModel(projectId, modelId, input, options) {
      return client.request(
        modelPath(projectId, `/${requireIdentifier(modelId, "model_id")}`),
        {
          method: "PATCH",
          signal: options.signal,
          headers: writeHeaders(options),
          body: input,
          validate: (value) => assertProjectScope(parseModel(value), projectId, "模型"),
        },
      );
    },
    async actModel(projectId, modelId, action, options) {
      return client.request(
        modelPath(
          projectId,
          `/${requireIdentifier(modelId, "model_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) => assertProjectScope(parseModel(value), projectId, "模型"),
        },
      );
    },
    async listVersions(projectId, modelId, options) {
      const path = modelPath(
        projectId,
        `/${requireIdentifier(modelId, "model_id")}/versions`,
      );
      return client.get(path, {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseModelVersionPage(value), projectId, "模型版本列表"),
      });
    },
    async createVersion(projectId, modelId, input, options) {
      const path = modelPath(
        projectId,
        `/${requireIdentifier(modelId, "model_id")}/versions`,
      );
      return client.request(path, {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseModelVersion(value), projectId, "模型版本"),
      });
    },
    async getVersion(projectId, modelId, versionId, options) {
      const path = modelPath(
        projectId,
        `/${requireIdentifier(modelId, "model_id")}/versions/${requireIdentifier(versionId, "model_version_id")}`,
      );
      return client.get(path, {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseModelVersion(value), projectId, "模型版本"),
      });
    },
    async actVersion(projectId, modelId, versionId, action, options) {
      const path = modelPath(
        projectId,
        `/${requireIdentifier(modelId, "model_id")}/versions/${requireIdentifier(versionId, "model_version_id")}/actions/${requireIdentifier(action, "action")}`,
      );
      return client.request(path, {
        method: "POST",
        signal: options.signal,
        headers: writeHeaders(options),
        body: { reason: options.reason ?? null },
        validate: (value) =>
          assertProjectScope(parseModelVersion(value), projectId, "模型版本"),
      });
    },
    async listEvaluations(projectId, options) {
      return client.get(evaluationPath(projectId), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseEvaluationPage(value), projectId, "评测列表"),
      });
    },
    async createEvaluation(projectId, input, options) {
      return client.request(evaluationPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseEvaluation(value), projectId, "评测"),
      });
    },
    async getEvaluation(projectId, evaluationId, options) {
      return client.get(
        evaluationPath(projectId, `/${requireIdentifier(evaluationId, "evaluation_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseEvaluation(value), projectId, "评测"),
        },
      );
    },
    async actEvaluation(projectId, evaluationId, action, options) {
      return client.request(
        evaluationPath(
          projectId,
          `/${requireIdentifier(evaluationId, "evaluation_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseEvaluation(value), projectId, "评测"),
        },
      );
    },
    async listServices(projectId, options) {
      return client.get(inferencePath(projectId, "/inference-services"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseInferenceServicePage(value), projectId, "推理服务列表"),
      });
    },
    async createService(projectId, input, options) {
      return client.request(inferencePath(projectId, "/inference-services"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseInferenceService(value), projectId, "推理服务"),
      });
    },
    async getService(projectId, serviceId, options) {
      return client.get(
        inferencePath(projectId, `/inference-services/${requireIdentifier(serviceId, "service_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseInferenceService(value), projectId, "推理服务"),
        },
      );
    },
    async actService(projectId, serviceId, action, options) {
      return client.request(
        inferencePath(
          projectId,
          `/inference-services/${requireIdentifier(serviceId, "service_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseInferenceService(value), projectId, "推理服务"),
        },
      );
    },
    async listTasks(projectId, options) {
      return client.get(inferencePath(projectId, "/inference-tasks"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseInferenceTaskPage(value), projectId, "推理任务列表"),
      });
    },
    async createTask(projectId, input, options) {
      return client.request(inferencePath(projectId, "/inference-tasks"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseInferenceTask(value), projectId, "推理任务"),
      });
    },
    async getTask(projectId, taskId, options) {
      return client.get(
        inferencePath(projectId, `/inference-tasks/${requireIdentifier(taskId, "task_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseInferenceTask(value), projectId, "推理任务"),
        },
      );
    },
    async actTask(projectId, taskId, action, options) {
      return client.request(
        inferencePath(
          projectId,
          `/inference-tasks/${requireIdentifier(taskId, "task_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseInferenceTask(value), projectId, "推理任务"),
        },
      );
    },
    async listFeedback(projectId, options) {
      return client.get(feedbackPath(projectId), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseFeedbackPage(value), projectId, "模型反馈列表"),
      });
    },
    async createFeedback(projectId, input, options) {
      return client.request(feedbackPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseFeedback(value), projectId, "模型反馈"),
      });
    },
    async getFeedback(projectId, feedbackId, options) {
      return client.get(
        feedbackPath(projectId, `/${requireIdentifier(feedbackId, "feedback_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseFeedback(value), projectId, "模型反馈"),
        },
      );
    },
    async actFeedback(projectId, feedbackId, action, options) {
      return client.request(
        feedbackPath(
          projectId,
          `/${requireIdentifier(feedbackId, "feedback_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: toActionBody(options),
          validate: (value) =>
            assertProjectScope(parseFeedback(value), projectId, "模型反馈"),
        },
      );
    },
    async getDataGaps(projectId, options) {
      return client.get(projectPath(projectId, "/data-gaps"), {
        signal: options?.signal,
        query: { dimension_type: options?.dimension_type },
        validate: parseGapSummary,
      });
    },
    async listRequirements(projectId, options) {
      return client.get(requirementPath(projectId), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseRequirementPage(value), projectId, "数据需求列表"),
      });
    },
    async createRequirement(projectId, input, options) {
      return client.request(requirementPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseRequirement(value), projectId, "数据需求"),
      });
    },
    async getRequirement(projectId, requirementId, options) {
      return client.get(
        requirementPath(
          projectId,
          `/${requireIdentifier(requirementId, "requirement_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseRequirement(value), projectId, "数据需求"),
        },
      );
    },
    async actRequirement(projectId, requirementId, action, options) {
      return client.request(
        requirementPath(
          projectId,
          `/${requireIdentifier(requirementId, "requirement_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: toActionBody(options),
          validate: (value) =>
            assertProjectScope(parseRequirement(value), projectId, "数据需求"),
        },
      );
    },
  };
}

export const modelOpsApi = createModelOpsApi(apiClient);
