/**
 * 标注 Schema 与样本集接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-schemas`、`/annotation-sample-sets` 两条资源。
 * 写动作（新建 / 编辑 / 发布 / 废弃 / 快照 / 取消）已接入真实后端路由；
 * 服务端仍负责状态迁移、权限与项目隔离校验。
 */
import type { CursorPage } from "../types/api";
import type {
  AnnotationSampleSet,
  AnnotationSchema,
  LabelDefinition,
} from "../types/annotation";
import type { WithAllowedActions } from "./annotationAllowedActions";
import type { ApiClient } from "./api";
import {
  parseAnnotationSampleSet,
  parseAnnotationSampleSetPage,
  parseAnnotationSchema,
  parseAnnotationSchemaPage,
} from "./annotationParsers";
import {
  createHeaders,
  writeHeaders,
  type AnnotationCreateOptions,
  type AnnotationListOptions,
  type AnnotationWriteOptions,
  type DetailRequestOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
} from "./projectScope";

/** 新建 Schema（初始 DRAFT）。字段与后端 `AnnotationSchemaCreate` 对齐。 */
export interface AnnotationSchemaCreateInput {
  code: string;
  name: string;
  description?: string | null;
  annotation_levels: string[];
  labels: LabelDefinition[];
  compatibility?: string;
  schema_lineage_id?: string | null;
  schema_version?: number;
  supersedes_id?: string | null;
}

/** 编辑 Schema；服务端只允许修改 DRAFT。 */
export interface AnnotationSchemaPatchInput {
  name?: string;
  description?: string | null;
  annotation_levels?: string[];
  labels?: LabelDefinition[];
  compatibility?: string;
}

/** 新建样本集。`filters.include_episode_ids` 可显式点名 Episode。 */
export interface AnnotationSampleSetCreateInput {
  code: string;
  name?: string | null;
  strategy?: string;
  selection_rule_version?: string;
  filters?: Record<string, unknown>;
  strata?: Record<string, unknown>[];
  dedup_key?: string;
  data_source?: string;
  warn_confirmed?: boolean;
}

/** 样本集动作：`cancel` 必须带原因；`preview` 可改过滤条件。 */
export interface AnnotationSampleSetActionOptions extends AnnotationWriteOptions {
  filters?: Record<string, unknown> | null;
  strata?: Record<string, unknown>[] | null;
  warnConfirmed?: boolean;
}

export interface AnnotationSchemaApi {
  listSchemas: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<WithAllowedActions<AnnotationSchema>>>;
  getSchema: (
    projectId: string,
    schemaId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<AnnotationSchema>>;
  createSchema: (
    projectId: string,
    input: AnnotationSchemaCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<AnnotationSchema>>;
  updateSchema: (
    projectId: string,
    schemaId: string,
    input: AnnotationSchemaPatchInput,
    options: AnnotationWriteOptions,
  ) => Promise<WithAllowedActions<AnnotationSchema>>;
  actOnSchema: (
    projectId: string,
    schemaId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<WithAllowedActions<AnnotationSchema>>;
  listSampleSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<WithAllowedActions<AnnotationSampleSet>>>;
  getSampleSet: (
    projectId: string,
    sampleSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<AnnotationSampleSet>>;
  createSampleSet: (
    projectId: string,
    input: AnnotationSampleSetCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<AnnotationSampleSet>>;
  actOnSampleSet: (
    projectId: string,
    sampleSetId: string,
    action: string,
    options: AnnotationSampleSetActionOptions,
  ) => Promise<WithAllowedActions<AnnotationSampleSet>>;
}

function schemaPath(projectId: string, schemaId: string): string {
  return projectPath(
    projectId,
    `/annotation-schemas/${requireIdentifier(schemaId, "schema_id")}`,
  );
}

function sampleSetPath(projectId: string, sampleSetId: string): string {
  return projectPath(
    projectId,
    `/annotation-sample-sets/${requireIdentifier(sampleSetId, "sample_set_id")}`,
  );
}

export function createAnnotationSchemaApi(
  client: ApiClient,
): AnnotationSchemaApi {
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
      return client.get(schemaPath(projectId, schemaId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(
            parseAnnotationSchema(value),
            projectId,
            "标注 Schema",
          ),
      });
    },

    async createSchema(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-schemas"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseAnnotationSchema(value), projectId, "标注 Schema"),
      });
    },

    async updateSchema(projectId, schemaId, input, options) {
      return client.request(schemaPath(projectId, schemaId), {
        method: "PATCH",
        signal: options.signal,
        headers: writeHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseAnnotationSchema(value), projectId, "标注 Schema"),
      });
    },

    async actOnSchema(projectId, schemaId, action, options) {
      return client.request(
        `${schemaPath(projectId, schemaId)}/actions/${requireIdentifier(action, "action")}`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationSchema(value),
              projectId,
              "标注 Schema",
            ),
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
      return client.get(sampleSetPath(projectId, sampleSetId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(
            parseAnnotationSampleSet(value),
            projectId,
            "标注样本集",
          ),
      });
    },

    async createSampleSet(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-sample-sets"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            parseAnnotationSampleSet(value),
            projectId,
            "标注样本集",
          ),
      });
    },

    async actOnSampleSet(projectId, sampleSetId, action, options) {
      const body: Record<string, unknown> = {
        reason: options.reason ?? null,
        warn_confirmed: options.warnConfirmed ?? false,
      };
      if (options.filters !== undefined) {
        body.filters = options.filters;
      }
      if (options.strata !== undefined) {
        body.strata = options.strata;
      }
      return client.request(
        `${sampleSetPath(projectId, sampleSetId)}/actions/${requireIdentifier(action, "action")}`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationSampleSet(value),
              projectId,
              "标注样本集",
            ),
        },
      );
    },
  };
}
