/** 阶段 6 供应商、到货批次、片段裁决与验收账本 API。 */

import type { CursorPage } from "../types/api";
import type {
  AcceptanceLedgerEntry,
  DeliveryBatch,
  InboundSegment,
  Supplier,
  SupplierMetrics,
} from "../types/supplier";
import { apiClient, type ApiClient } from "./api";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";
import {
  parseBatch,
  parseBatchPage,
  parseLedgerPage,
  parseSegment,
  parseSegmentPage,
  parseSupplier,
  parseSupplierMetrics,
  parseSupplierPage,
} from "./supplierParsers";

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface WriteOptions {
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
  reason?: string | null;
}

export interface SupplierCreateInput {
  code: string;
  name: string;
  category?: string;
  contact_name?: string | null;
  contact_email?: string | null;
  external_ref?: string | null;
  notes?: string | null;
}

export interface BatchCreateInput {
  supplier_id: string;
  batch_code: string;
  title?: string | null;
  planned_segment_count?: number;
  planned_arrival_date?: string | null;
  storage_prefix?: string | null;
  notes?: string | null;
}

export interface SegmentCreateInput {
  segment_code: string;
  raw_asset_id?: string | null;
  duration_ns?: number | null;
  task_name?: string | null;
  diagnostics?: Record<string, unknown>;
}

export interface SegmentDecisionOptions extends WriteOptions {
  decision: string;
  reason?: string | null;
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

function supplierPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/suppliers${suffix}`);
}

function batchPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/inbound-batches${suffix}`);
}

export interface SupplierApi {
  listSuppliers(
    projectId: string,
    options?: PageRequestOptions & { status?: string; category?: string },
  ): Promise<CursorPage<Supplier>>;
  createSupplier(
    projectId: string,
    input: SupplierCreateInput,
    options?: CreateOptions,
  ): Promise<Supplier>;
  getSupplier(
    projectId: string,
    supplierId: string,
    options?: { signal?: AbortSignal },
  ): Promise<Supplier>;
  actSupplier(
    projectId: string,
    supplierId: string,
    action: string,
    options: WriteOptions,
  ): Promise<Supplier>;
  getMetrics(
    projectId: string,
    supplierId: string,
    options?: { signal?: AbortSignal },
  ): Promise<SupplierMetrics>;
  listBatches(
    projectId: string,
    options?: PageRequestOptions & { status?: string; supplier_id?: string },
  ): Promise<CursorPage<DeliveryBatch>>;
  createBatch(
    projectId: string,
    input: BatchCreateInput,
    options?: CreateOptions,
  ): Promise<DeliveryBatch>;
  getBatch(
    projectId: string,
    batchId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DeliveryBatch>;
  actBatch(
    projectId: string,
    batchId: string,
    action: string,
    options: WriteOptions,
  ): Promise<DeliveryBatch>;
  finalizeBatch(
    projectId: string,
    batchId: string,
    options: WriteOptions,
  ): Promise<DeliveryBatch>;
  listSegments(
    projectId: string,
    batchId: string,
    options?: PageRequestOptions & { status?: string },
  ): Promise<CursorPage<InboundSegment>>;
  addSegment(
    projectId: string,
    batchId: string,
    input: SegmentCreateInput,
    options?: CreateOptions,
  ): Promise<InboundSegment>;
  getSegment(
    projectId: string,
    batchId: string,
    segmentId: string,
    options?: { signal?: AbortSignal },
  ): Promise<InboundSegment>;
  actSegment(
    projectId: string,
    batchId: string,
    segmentId: string,
    action: string,
    options: WriteOptions,
  ): Promise<InboundSegment>;
  decideSegment(
    projectId: string,
    batchId: string,
    segmentId: string,
    options: SegmentDecisionOptions,
  ): Promise<InboundSegment>;
  listLedger(
    projectId: string,
    options?: PageRequestOptions & { batch_id?: string; supplier_id?: string },
  ): Promise<CursorPage<AcceptanceLedgerEntry>>;
}

export function createSupplierApi(client: ApiClient): SupplierApi {
  const batchOf = (projectId: string, batchId: string, suffix = "") =>
    batchPath(projectId, `/${requireIdentifier(batchId, "batch_id")}${suffix}`);
  const supplierOf = (projectId: string, supplierId: string, suffix = "") =>
    supplierPath(projectId, `/${requireIdentifier(supplierId, "supplier_id")}${suffix}`);

  return {
    async listSuppliers(projectId, options) {
      return client.get(supplierPath(projectId), {
        signal: options?.signal,
        query: {
          status: options?.status,
          category: options?.category,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseSupplierPage(value), projectId, "供应商列表"),
      });
    },
    async createSupplier(projectId, input, options) {
      return client.request(supplierPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseSupplier(value), projectId, "供应商"),
      });
    },
    async getSupplier(projectId, supplierId, options) {
      return client.get(supplierOf(projectId, supplierId), {
        signal: options?.signal,
        validate: (value) => assertProjectScope(parseSupplier(value), projectId, "供应商"),
      });
    },
    async actSupplier(projectId, supplierId, action, options) {
      return client.request(
        supplierOf(
          projectId,
          supplierId,
          `/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) => assertProjectScope(parseSupplier(value), projectId, "供应商"),
        },
      );
    },
    async getMetrics(projectId, supplierId, options) {
      return client.get(supplierOf(projectId, supplierId, "/metrics"), {
        signal: options?.signal,
        validate: parseSupplierMetrics,
      });
    },
    async listBatches(projectId, options) {
      return client.get(batchPath(projectId), {
        signal: options?.signal,
        query: {
          status: options?.status,
          supplier_id: options?.supplier_id,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseBatchPage(value), projectId, "到货批次列表"),
      });
    },
    async createBatch(projectId, input, options) {
      return client.request(batchPath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseBatch(value), projectId, "到货批次"),
      });
    },
    async getBatch(projectId, batchId, options) {
      return client.get(batchOf(projectId, batchId), {
        signal: options?.signal,
        validate: (value) => assertProjectScope(parseBatch(value), projectId, "到货批次"),
      });
    },
    async actBatch(projectId, batchId, action, options) {
      return client.request(
        batchOf(projectId, batchId, `/actions/${requireIdentifier(action, "action")}`),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) => assertProjectScope(parseBatch(value), projectId, "到货批次"),
        },
      );
    },
    async finalizeBatch(projectId, batchId, options) {
      return client.request(batchOf(projectId, batchId, "/finalize"), {
        method: "POST",
        signal: options.signal,
        headers: writeHeaders(options),
        body: { reason: options.reason ?? null },
        validate: (value) => assertProjectScope(parseBatch(value), projectId, "到货批次"),
      });
    },
    async listSegments(projectId, batchId, options) {
      return client.get(batchOf(projectId, batchId, "/segments"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(parseSegmentPage(value), projectId, "片段列表"),
      });
    },
    async addSegment(projectId, batchId, input, options) {
      return client.request(batchOf(projectId, batchId, "/segments"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) => assertProjectScope(parseSegment(value), projectId, "片段"),
      });
    },
    async getSegment(projectId, batchId, segmentId, options) {
      return client.get(
        batchOf(projectId, batchId, `/segments/${requireIdentifier(segmentId, "segment_id")}`),
        {
          signal: options?.signal,
          validate: (value) => assertProjectScope(parseSegment(value), projectId, "片段"),
        },
      );
    },
    async actSegment(projectId, batchId, segmentId, action, options) {
      return client.request(
        batchOf(
          projectId,
          batchId,
          `/segments/${requireIdentifier(segmentId, "segment_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) => assertProjectScope(parseSegment(value), projectId, "片段"),
        },
      );
    },
    async decideSegment(projectId, batchId, segmentId, options) {
      return client.request(
        batchOf(
          projectId,
          batchId,
          `/segments/${requireIdentifier(segmentId, "segment_id")}/decision`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { decision: options.decision, reason: options.reason ?? null },
          validate: (value) => assertProjectScope(parseSegment(value), projectId, "片段"),
        },
      );
    },
    async listLedger(projectId, options) {
      return client.get(projectPath(projectId, "/acceptance-ledger"), {
        signal: options?.signal,
        query: {
          batch_id: options?.batch_id,
          supplier_id: options?.supplier_id,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseLedgerPage(value), projectId, "验收账本"),
      });
    },
  };
}

export const supplierApi = createSupplierApi(apiClient);
