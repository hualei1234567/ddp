/** 阶段 7 数据平面：外部数据源与导入任务 API。 */

import type { CursorPage } from "../types/api";
import type {
  DataReview,
  DataSource,
  DataSourceDetail,
  DataSourceImport,
  ImportLogList,
  MediaAsset,
  MediaPlayback,
  QualityReport,
  ReviewEvidence,
} from "../types/datasource";
import { ApiError, apiClient, type ApiClient } from "./api";
import {
  parseDataReview,
  parseDataSource,
  parseDataSourceDetail,
  parseDataSourceImport,
  parseImportLogList,
  parseMediaAsset,
  parseMediaPlayback,
  parseQualityReport,
} from "./datasourceParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";
import { asRecord as asRecordLike, parseCursorPage } from "./validation";

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface WriteOptions {
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface DataSourceCreateInput {
  code: string;
  name: string;
  repo_id: string;
  /** 必须 40 位 commit sha；服务端拒绝 main 等分支名。 */
  revision: string;
  license: string;
  access_condition: string;
  is_gated?: boolean;
  config_name?: string | null;
  split?: string | null;
  dataset_card_url?: string | null;
  notes?: string | null;
  /** 显式请求远端校验；默认 false，不会访问外网。 */
  verify_remote?: boolean;
}

export interface DataSourceDetailOptions {
  signal?: AbortSignal;
  importLimit?: number;
}

export interface DataSourcePatchInput {
  name?: string | null;
  license?: string | null;
  access_condition?: string | null;
  is_gated?: boolean | null;
  config_name?: string | null;
  split?: string | null;
  notes?: string | null;
}

export interface ImportCreateInput {
  scope: string;
  tool_name: string;
  tool_version: string;
  sample_limit?: number | null;
  cache_location?: string | null;
}

export interface ReviewDecisionInput {
  decision: string;
  reason?: string | null;
  evidence?: ReviewEvidence | null;
}

export interface ImportActionOptions extends WriteOptions {
  row_count?: number | null;
  file_count?: number | null;
  error_message?: string | null;
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

function sourcePath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/data-sources${suffix}`);
}

function importPath(projectId: string, suffix = ""): string {
  return projectPath(projectId, `/data-source-imports${suffix}`);
}

export interface DataSourceApi {
  listDataSources(
    projectId: string,
    options?: PageRequestOptions & { kind?: string },
  ): Promise<CursorPage<DataSource>>;
  createDataSource(
    projectId: string,
    input: DataSourceCreateInput,
    options?: CreateOptions,
  ): Promise<DataSource>;
  getDataSource(
    projectId: string,
    dataSourceId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DataSource>;
  getDataSourceDetail(
    projectId: string,
    dataSourceId: string,
    options?: DataSourceDetailOptions,
  ): Promise<DataSourceDetail>;
  updateDataSource(
    projectId: string,
    dataSourceId: string,
    input: DataSourcePatchInput,
    options: WriteOptions,
  ): Promise<DataSource>;
  createImport(
    projectId: string,
    dataSourceId: string,
    input: ImportCreateInput,
    options?: CreateOptions,
  ): Promise<DataSourceImport>;
  listImports(
    projectId: string,
    options?: PageRequestOptions & {
      data_source_id?: string;
      status?: string;
    },
  ): Promise<CursorPage<DataSourceImport>>;
  getImport(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DataSourceImport>;
  listImportLogs(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal; limit?: number },
  ): Promise<ImportLogList>;
  actImport(
    projectId: string,
    importId: string,
    action: string,
    options: ImportActionOptions,
  ): Promise<DataSourceImport>;
  listImportAssets(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal; limit?: number },
  ): Promise<MediaAsset[]>;
  playback(
    projectId: string,
    assetId: string,
    options?: { signal?: AbortSignal },
  ): Promise<MediaPlayback>;
  ensureReview(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DataReview>;
  getReview(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal },
  ): Promise<DataReview | null>;
  decideReview(
    projectId: string,
    importId: string,
    input: ReviewDecisionInput,
    options: WriteOptions,
  ): Promise<DataReview>;
  qualityReport(
    projectId: string,
    importId: string,
    options?: { signal?: AbortSignal },
  ): Promise<QualityReport>;
}

export function createDataSourceApi(client: ApiClient): DataSourceApi {
  const sourceOf = (projectId: string, id: string, suffix = "") =>
    sourcePath(projectId, `/${requireIdentifier(id, "data_source_id")}${suffix}`);
  const importOf = (projectId: string, id: string, suffix = "") =>
    importPath(projectId, `/${requireIdentifier(id, "import_id")}${suffix}`);

  return {
    async listDataSources(projectId, options) {
      return client.get(sourcePath(projectId), {
        signal: options?.signal,
        query: { kind: options?.kind, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            parseCursorPage(value, "数据源列表", (item) =>
              parseDataSource(item),
            ),
            projectId,
            "数据源列表",
          ),
      });
    },
    async createDataSource(projectId, input, options) {
      return client.request(sourcePath(projectId), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseDataSource(value), projectId, "数据源"),
      });
    },
    async getDataSource(projectId, dataSourceId, options) {
      return client.get(sourceOf(projectId, dataSourceId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseDataSource(value), projectId, "数据源"),
      });
    },
    async getDataSourceDetail(projectId, dataSourceId, options) {
      return client.get(sourceOf(projectId, dataSourceId, "/detail"), {
        signal: options?.signal,
        query: { import_limit: options?.importLimit },
        validate: (value) => {
          const detail = parseDataSourceDetail(value);
          assertProjectScope(detail.data_source, projectId, "数据源");
          detail.imports.forEach((item, index) => {
            assertProjectScope(item, projectId, `数据源详情.imports[${index}]`);
          });
          return detail;
        },
      });
    },
    async updateDataSource(projectId, dataSourceId, input, options) {
      return client.request(sourceOf(projectId, dataSourceId), {
        method: "PATCH",
        signal: options.signal,
        headers: writeHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseDataSource(value), projectId, "数据源"),
      });
    },
    async createImport(projectId, dataSourceId, input, options) {
      return client.request(sourceOf(projectId, dataSourceId, "/imports"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            parseDataSourceImport(value),
            projectId,
            "导入任务",
          ),
      });
    },
    async listImports(projectId, options) {
      return client.get(importPath(projectId), {
        signal: options?.signal,
        query: {
          data_source_id: options?.data_source_id,
          status: options?.status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            parseCursorPage(value, "导入任务列表", (item) =>
              parseDataSourceImport(item),
            ),
            projectId,
            "导入任务列表",
          ),
      });
    },
    async getImport(projectId, importId, options) {
      return client.get(importOf(projectId, importId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(
            parseDataSourceImport(value),
            projectId,
            "导入任务",
          ),
      });
    },
    async listImportLogs(projectId, importId, options) {
      return client.get(importOf(projectId, importId, "/logs"), {
        signal: options?.signal,
        query: { limit: options?.limit },
        validate: (value) => {
          const page = parseImportLogList(value);
          page.items.forEach((item, index) => {
            assertProjectScope(item, projectId, `导入日志列表.items[${index}]`);
            if (item.import_id !== importId) {
              throw new ApiError({
                kind: "invalid_response",
                message: `导入日志列表.items[${index}].import_id 与请求不一致`,
              });
            }
          });
          return page;
        },
      });
    },
    async listImportAssets(projectId, importId, options) {
      return client.get(importOf(projectId, importId, "/assets"), {
        signal: options?.signal,
        query: { limit: options?.limit },
        validate: (value) => {
          const record = asRecordLike(value, "媒体资产列表");
          const items = record.items;
          if (!Array.isArray(items)) {
            throw new Error("items 必须是数组");
          }
          return items.map((item) => {
            const asset = parseMediaAsset(item);
            assertProjectScope(asset, projectId, "媒体资产");
            return asset;
          });
        },
      });
    },
    async playback(projectId, assetId, options) {
      return client.request(
        projectPath(projectId, `/media-assets/${requireIdentifier(assetId, "asset_id")}/playback`),
        {
          method: "POST",
          signal: options?.signal,
          body: {},
          validate: (value) => parseMediaPlayback(value),
        },
      );
    },
    async ensureReview(projectId, importId, options) {
      return client.request(importOf(projectId, importId, "/review"), {
        method: "POST",
        signal: options?.signal,
        body: {},
        validate: (value) =>
          assertProjectScope(parseDataReview(value), projectId, "审核记录"),
      });
    },
    async getReview(projectId, importId, options) {
      // 尚未创建审核记录时服务端返回 404；这不是错误状态，调用方按 null 处理。
      try {
        return await client.get(importOf(projectId, importId, "/review"), {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseDataReview(value), projectId, "审核记录"),
        });
      } catch (error) {
        if (error instanceof ApiError && error.isNotFound) {
          return null;
        }
        throw error;
      }
    },
    async decideReview(projectId, importId, input, options) {
      return client.request(importOf(projectId, importId, "/review/decisions"), {
        method: "POST",
        signal: options.signal,
        headers: {
          "If-Match": formatIfMatch(options.version),
          "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
        },
        body: {
          decision: input.decision,
          reason: input.reason ?? null,
          evidence: input.evidence ?? null,
        },
        validate: (value) =>
          assertProjectScope(parseDataReview(value), projectId, "审核记录"),
      });
    },
    async qualityReport(projectId, importId, options) {
      return client.get(importOf(projectId, importId, "/quality-report"), {
        signal: options?.signal,
        validate: (value) => {
          const report = parseQualityReport(value);
          if (report.import_id !== importId) {
            throw new Error("质量报告的 import_id 与请求不一致");
          }
          return report;
        },
      });
    },
    async actImport(projectId, importId, action, options) {
      return client.request(
        importOf(
          projectId,
          importId,
          `/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {
            row_count: options.row_count ?? null,
            file_count: options.file_count ?? null,
            error_message: options.error_message ?? null,
          },
          validate: (value) =>
            assertProjectScope(
              parseDataSourceImport(value),
              projectId,
              "导入任务",
            ),
        },
      );
    },
  };
}

export const dataSourceApi = createDataSourceApi(apiClient);
