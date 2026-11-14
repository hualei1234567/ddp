/** 阶段 7 数据平面：外部数据源与导入任务 API。 */

import type { CursorPage } from "../types/api";
import type { DataSource, DataSourceImport } from "../types/datasource";
import { apiClient, type ApiClient } from "./api";
import {
  parseDataSource,
  parseDataSourceImport,
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
import { parseCursorPage } from "./validation";

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
  actImport(
    projectId: string,
    importId: string,
    action: string,
    options: ImportActionOptions,
  ): Promise<DataSourceImport>;
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
