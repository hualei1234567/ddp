/** 阶段 4 训练需求、交付与外部训练引用 API。 */

import type { CursorPage } from "../types/api";
import type {
  ExternalTrainingReference,
  RequirementSnapshot,
  TrainingDelivery,
  TrainingRequest,
  TrainingRequestRevision,
} from "../types/dataset";
import { apiClient, type ApiClient } from "./api";
import {
  parseExternalTrainingReference,
  parseExternalTrainingReferencePage,
  parseTrainingDelivery,
  parseTrainingDeliveryPage,
  parseTrainingRequest,
  parseTrainingRequestPage,
  parseTrainingRequestRevision,
  parseTrainingRequestRevisionPage,
} from "./datasetParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface WriteOptions {
  version: number;
  reason?: string | null;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface TrainingRequestCreateInput {
  code: string;
  title: string;
  owner_id: string;
  priority: string;
  snapshot: RequirementSnapshot;
}

export interface DeliveryCreateInput {
  training_request_id: string;
  training_request_revision_id: string;
  dataset_version_id: string;
  export_job_id: string;
  recipient_id: string;
  delivery_note_no: string;
  recipient_team?: string | null;
}

export interface ExternalReferenceCreateInput {
  adapter_id: string;
  adapter_version: string;
  correlation_id: string;
  model_ref?: Record<string, unknown> | null;
  evaluation_ref?: Record<string, unknown> | null;
  config?: Record<string, unknown>;
  /**
   * 可选显式执行方式。Adapter 已配置时以配置为准且必须一致；服务端两边都没有
   * 时返回 422——前端不得自行补一个默认值，尤其不能默认 REMOTE。
   */
  execution_mode?: "LOCAL" | "MOCK" | "DRY_RUN" | "REMOTE";
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

export interface TrainingApi {
  listRequests(
    projectId: string,
    options?: PageRequestOptions & { status?: string; priority?: string },
  ): Promise<CursorPage<TrainingRequest>>;
  createRequest(
    projectId: string,
    input: TrainingRequestCreateInput,
    options?: CreateOptions,
  ): Promise<TrainingRequest>;
  getRequest(
    projectId: string,
    requestId: string,
    options?: { signal?: AbortSignal },
  ): Promise<TrainingRequest>;
  actRequest(
    projectId: string,
    requestId: string,
    action: string,
    options: WriteOptions,
  ): Promise<TrainingRequest>;
  listRevisions(
    projectId: string,
    requestId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<TrainingRequestRevision>>;
  createRevision(
    projectId: string,
    requestId: string,
    input: { snapshot: RequirementSnapshot; change_reason: string },
    options?: CreateOptions,
  ): Promise<TrainingRequestRevision>;
  getRevision(
    projectId: string,
    requestId: string,
    revisionId: string,
    options?: { signal?: AbortSignal },
  ): Promise<TrainingRequestRevision>;
  actRevision(
    projectId: string,
    requestId: string,
    revisionId: string,
    action: string,
    options: WriteOptions,
  ): Promise<TrainingRequestRevision>;
  listDeliveries(
    projectId: string,
    options?: PageRequestOptions & { status?: string; training_request_id?: string },
  ): Promise<CursorPage<TrainingDelivery>>;
  createDelivery(
    projectId: string,
    input: DeliveryCreateInput,
    options?: CreateOptions,
  ): Promise<TrainingDelivery>;
  getDelivery(
    projectId: string,
    deliveryId: string,
    options?: { signal?: AbortSignal },
  ): Promise<TrainingDelivery>;
  actDelivery(
    projectId: string,
    deliveryId: string,
    action: string,
    options: WriteOptions & { acceptance_opinion?: string | null; rework_ref?: string | null },
  ): Promise<TrainingDelivery>;
  listExternalReferences(
    projectId: string,
    deliveryId: string,
    options?: PageRequestOptions,
  ): Promise<CursorPage<ExternalTrainingReference>>;
  createExternalReference(
    projectId: string,
    deliveryId: string,
    input: ExternalReferenceCreateInput,
    options?: CreateOptions,
  ): Promise<ExternalTrainingReference>;
  getExternalReference(
    projectId: string,
    externalRefId: string,
    options?: { signal?: AbortSignal },
  ): Promise<ExternalTrainingReference>;
  actExternalReference(
    projectId: string,
    externalRefId: string,
    action: string,
    options: WriteOptions,
  ): Promise<ExternalTrainingReference>;
}

export function createTrainingApi(client: ApiClient): TrainingApi {
  const requestPath = (projectId: string, requestId: string, suffix = "") =>
    projectPath(
      projectId,
      `/training-requests/${requireIdentifier(requestId, "request_id")}${suffix}`,
    );
  const deliveryPath = (projectId: string, deliveryId: string, suffix = "") =>
    projectPath(
      projectId,
      `/training-deliveries/${requireIdentifier(deliveryId, "delivery_id")}${suffix}`,
    );
  const externalPath = (projectId: string, externalRefId: string, suffix = "") =>
    projectPath(
      projectId,
      `/external-training-references/${requireIdentifier(externalRefId, "external_ref_id")}${suffix}`,
    );

  return {
    async listRequests(projectId, options) {
      return client.get(projectPath(projectId, "/training-requests"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          priority: options?.priority,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseTrainingRequestPage(value), projectId, "训练需求列表"),
      });
    },
    async createRequest(projectId, input, options) {
      return client.request(projectPath(projectId, "/training-requests"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseTrainingRequest(value), projectId, "训练需求"),
      });
    },
    async getRequest(projectId, requestId, options) {
      return client.get(requestPath(projectId, requestId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseTrainingRequest(value), projectId, "训练需求"),
      });
    },
    async actRequest(projectId, requestId, action, options) {
      return client.request(
        requestPath(projectId, requestId, `/actions/${requireIdentifier(action, "action")}`),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseTrainingRequest(value), projectId, "训练需求"),
        },
      );
    },
    async listRevisions(projectId, requestId, options) {
      return client.get(requestPath(projectId, requestId, "/revisions"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(parseTrainingRequestRevisionPage(value), projectId, "训练需求修订列表"),
      });
    },
    async createRevision(projectId, requestId, input, options) {
      return client.request(requestPath(projectId, requestId, "/revisions"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseTrainingRequestRevision(value), projectId, "训练需求修订"),
      });
    },
    async getRevision(projectId, requestId, revisionId, options) {
      return client.get(
        requestPath(projectId, requestId, `/revisions/${requireIdentifier(revisionId, "revision_id")}`),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseTrainingRequestRevision(value), projectId, "训练需求修订"),
        },
      );
    },
    async actRevision(projectId, requestId, revisionId, action, options) {
      return client.request(
        requestPath(
          projectId,
          requestId,
          `/revisions/${requireIdentifier(revisionId, "revision_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseTrainingRequestRevision(value), projectId, "训练需求修订"),
        },
      );
    },
    async listDeliveries(projectId, options) {
      return client.get(projectPath(projectId, "/training-deliveries"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          training_request_id: options?.training_request_id,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseTrainingDeliveryPage(value), projectId, "训练交付列表"),
      });
    },
    async createDelivery(projectId, input, options) {
      return client.request(projectPath(projectId, "/training-deliveries"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseTrainingDelivery(value), projectId, "训练交付"),
      });
    },
    async getDelivery(projectId, deliveryId, options) {
      return client.get(deliveryPath(projectId, deliveryId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseTrainingDelivery(value), projectId, "训练交付"),
      });
    },
    async actDelivery(projectId, deliveryId, action, options) {
      return client.request(
        deliveryPath(projectId, deliveryId, `/actions/${requireIdentifier(action, "action")}`),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {
            reason: options.reason ?? null,
            acceptance_opinion: options.acceptance_opinion ?? null,
            rework_ref: options.rework_ref ?? null,
          },
          validate: (value) =>
            assertProjectScope(parseTrainingDelivery(value), projectId, "训练交付"),
        },
      );
    },
    async listExternalReferences(projectId, deliveryId, options) {
      return client.get(deliveryPath(projectId, deliveryId, "/external-references"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(
            parseExternalTrainingReferencePage(value),
            projectId,
            "外部训练引用列表",
          ),
      });
    },
    async createExternalReference(projectId, deliveryId, input, options) {
      return client.request(deliveryPath(projectId, deliveryId, "/external-references"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseExternalTrainingReference(value), projectId, "外部训练引用"),
      });
    },
    async getExternalReference(projectId, externalRefId, options) {
      return client.get(externalPath(projectId, externalRefId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseExternalTrainingReference(value), projectId, "外部训练引用"),
      });
    },
    async actExternalReference(projectId, externalRefId, action, options) {
      return client.request(
        externalPath(
          projectId,
          externalRefId,
          `/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {},
          validate: (value) =>
            assertProjectScope(parseExternalTrainingReference(value), projectId, "外部训练引用"),
        },
      );
    },
  };
}

export const trainingApi = createTrainingApi(apiClient);
