/**
 * 标注任务、批次与分配接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-tasks`、`/annotation-batches`、`/annotation-assignments`。
 * 任务动作走 `actions/{action}`，必须带 If-Match 与幂等键。
 */
import type { CursorPage } from "../types/api";
import type {
  AnnotationAssignment,
  AnnotationBatch,
  AnnotationTask,
  AnnotationTaskCreateInput,
} from "../types/annotation";
import type { ApiClient } from "./api";
import {
  parseAnnotationAssignment,
  parseAnnotationAssignmentPage,
  parseAnnotationBatch,
  parseAnnotationBatchPage,
  parseAnnotationTask,
  parseAnnotationTaskPage,
} from "./annotationParsers";
import {
  createHeaders,
  writeHeaders,
  type AnnotationCreateOptions,
  type AnnotationListOptions,
  type AnnotationWriteOptions,
  type AssignmentListOptions,
  type DetailRequestOptions,
} from "./annotationRequestBase";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface AnnotationTaskApi {
  listTasks: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<AnnotationTask>>;
  createTask: (
    projectId: string,
    input: AnnotationTaskCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<AnnotationTask>;
  getTask: (
    projectId: string,
    taskId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationTask>;
  actOnTask: (
    projectId: string,
    taskId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<AnnotationTask>;
  listBatches: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<AnnotationBatch>>;
  getBatch: (
    projectId: string,
    batchId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationBatch>;
  listAssignments: (
    projectId: string,
    options?: AssignmentListOptions,
  ) => Promise<CursorPage<AnnotationAssignment>>;
  getAssignment: (
    projectId: string,
    assignmentId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationAssignment>;
}

export function createAnnotationTaskApi(client: ApiClient): AnnotationTaskApi {
  return {
    async listTasks(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-tasks"), {
        signal: options?.signal,
        query: { status: options?.status, ...pageQuery(options) },
        validate: (value) =>
          assertPageScope(
            parseAnnotationTaskPage(value),
            projectId,
            "标注任务列表",
          ),
      });
    },

    async createTask(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-tasks"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseAnnotationTask(value), projectId, "标注任务"),
      });
    },

    async getTask(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationTask(value),
              projectId,
              "标注任务",
            ),
        },
      );
    },

    async actOnTask(projectId, taskId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(
              parseAnnotationTask(value),
              projectId,
              "标注任务",
            ),
        },
      );
    },

    async listBatches(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-tasks/${requireIdentifier(taskId, "task_id")}/batches`,
        ),
        {
          signal: options?.signal,
          query: pageQuery(options),
          validate: (value) =>
            assertPageScope(
              parseAnnotationBatchPage(value),
              projectId,
              "标注批次列表",
            ),
        },
      );
    },

    async getBatch(projectId, batchId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-batches/${requireIdentifier(batchId, "batch_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationBatch(value),
              projectId,
              "标注批次",
            ),
        },
      );
    },

    async listAssignments(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-assignments"), {
        signal: options?.signal,
        query: {
          task_id: options?.taskId,
          batch_id: options?.batchId,
          assignee_id: options?.assigneeId,
          status: options?.status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            parseAnnotationAssignmentPage(value),
            projectId,
            "标注分配列表",
          ),
      });
    },

    async getAssignment(projectId, assignmentId, options) {
      return client.get(
        projectPath(
          projectId,
          `/annotation-assignments/${requireIdentifier(assignmentId, "assignment_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              parseAnnotationAssignment(value),
              projectId,
              "标注分配",
            ),
        },
      );
    },
  };
}
