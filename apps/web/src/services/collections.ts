import type {
  CollectionBatchPage,
  CollectionTask,
  CollectionTaskCreateInput,
  CollectionTaskPage,
  EpisodePage,
} from "../types/collection";
import { apiClient, type ApiClient } from "./api";
import {
  parseCollectionBatchPage,
  parseCollectionTask,
  parseCollectionTaskPage,
  parseEpisodePage,
} from "./collectionParsers";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export interface TaskListOptions extends PageRequestOptions {
  status?: string;
  assignedTo?: string;
}

export interface EpisodeListOptions extends PageRequestOptions {
  status?: string;
  collectionTaskId?: string;
}

export interface TaskActionOptions {
  /** 服务端当前 version；写动作必须带 If-Match。 */
  version: number;
  reason?: string | null;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

/** 创建任务只需要幂等键与取消信号，没有 If-Match。 */
export interface TaskCreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface CollectionsApi {
  listTasks: (
    projectId: string,
    options?: TaskListOptions,
  ) => Promise<CollectionTaskPage>;
  createTask: (
    projectId: string,
    input: CollectionTaskCreateInput,
    options?: TaskCreateOptions,
  ) => Promise<CollectionTask>;
  getTask: (
    projectId: string,
    taskId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<CollectionTask>;
  actOnTask: (
    projectId: string,
    taskId: string,
    action: string,
    options: TaskActionOptions,
  ) => Promise<CollectionTask>;
  listBatches: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions,
  ) => Promise<CollectionBatchPage>;
  listEpisodes: (
    projectId: string,
    options?: EpisodeListOptions,
  ) => Promise<EpisodePage>;
}

/**
 * 阶段 2 采集任务/批次/Episode API。
 *
 * 端点来自 `openapi.stage2.v1.yaml`（DRAFT）。前端只负责路径、头部、
 * 响应结构校验与项目归属一致性检查；状态与权限以服务端为准。
 */
export function createCollectionsApi(client: ApiClient): CollectionsApi {
  return {
    async listTasks(projectId, options) {
      return client.get(projectPath(projectId, "/collection-tasks"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          assigned_to: options?.assignedTo,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            parseCollectionTaskPage(value),
            projectId,
            "采集任务列表",
          ),
      });
    },

    async createTask(projectId, input, options = {}) {
      return client.request(projectPath(projectId, "/collection-tasks"), {
        method: "POST",
        signal: options.signal,
        headers: {
          "Idempotency-Key":
            options.idempotencyKey ?? createIdempotencyKey(),
        },
        body: input,
        validate: (value) =>
          assertProjectScope(parseCollectionTask(value), projectId, "采集任务"),
      });
    },

    async getTask(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/collection-tasks/${requireIdentifier(taskId, "task_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(parseCollectionTask(value), projectId, "采集任务"),
        },
      );
    },

    async actOnTask(projectId, taskId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/collection-tasks/${requireIdentifier(taskId, "task_id")}/actions/${requireIdentifier(action, "action")}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: {
            "If-Match": formatIfMatch(options.version),
            "Idempotency-Key":
              options.idempotencyKey ?? createIdempotencyKey(),
          },
          body: { reason: options.reason ?? null },
          validate: (value) =>
            assertProjectScope(parseCollectionTask(value), projectId, "采集任务"),
        },
      );
    },

    async listBatches(projectId, taskId, options) {
      return client.get(
        projectPath(
          projectId,
          `/collection-tasks/${requireIdentifier(taskId, "task_id")}/batches`,
        ),
        {
          signal: options?.signal,
          query: pageQuery(options),
          validate: (value) =>
            assertPageScope(
              parseCollectionBatchPage(value),
              projectId,
              "采集批次列表",
            ),
        },
      );
    },

    async listEpisodes(projectId, options) {
      return client.get(projectPath(projectId, "/episodes"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          collection_task_id: options?.collectionTaskId,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(parseEpisodePage(value), projectId, "Episode 列表"),
      });
    },
  };
}

export const collectionsApi = createCollectionsApi(apiClient);
