/**
 * 标注 Schema 与样本集接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-schemas`、`/annotation-sample-sets` 两条只读资源。
 * 写动作不在前端开放，故这里只有 GET。
 */
import type { CursorPage } from "../types/api";
import type {
  AnnotationSampleSet,
  AnnotationSchema,
} from "../types/annotation";
import type { ApiClient } from "./api";
import {
  parseAnnotationSampleSet,
  parseAnnotationSampleSetPage,
  parseAnnotationSchema,
  parseAnnotationSchemaPage,
} from "./annotationParsers";
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

export interface AnnotationSchemaApi {
  listSchemas: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationSchema>>;
  getSchema: (
    projectId: string,
    schemaId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationSchema>;
  listSampleSets: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationSampleSet>>;
  getSampleSet: (
    projectId: string,
    sampleSetId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationSampleSet>;
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
      return client.get(
        projectPath(
          projectId,
          `/annotation-schemas/${requireIdentifier(schemaId, "schema_id")}`,
        ),
        {
          signal: options?.signal,
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
      return client.get(
        projectPath(
          projectId,
          `/annotation-sample-sets/${requireIdentifier(sampleSetId, "sample_set_id")}`,
        ),
        {
          signal: options?.signal,
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
