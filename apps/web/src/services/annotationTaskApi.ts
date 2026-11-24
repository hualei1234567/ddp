/**
 * 标注任务、批次与分配接口（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：`/annotation-tasks`、`/annotation-batches`、`/annotation-assignments`。
 * 任务/批次/分配动作走 `actions/{action}`，必须带 If-Match 与幂等键。
 */
import type { CursorPage } from "../types/api";
import type {
  AssignmentCapacityPreview,
  AssignmentCapacityPreviewRequest,
} from "../types/annotationCapacityPreview";
import type {
  AnnotationAssignment,
  AnnotationBatch,
  AnnotationTask,
  AnnotationTaskCreateInput,
  DoubleAnnotationConsistency,
} from "../types/annotation";
import type { AnnotationOverdueSummary } from "../types/annotationOverdue";
import type { AnnotationWorkload } from "../types/annotationWorkload";
import type { WithAllowedActions } from "./annotationAllowedActions";
import type { ApiClient } from "./api";
import { parseAssignmentCapacityPreviewList } from "./annotationCapacityPreviewParsers";
import { parseAnnotationOverdueSummary } from "./annotationOverdueParsers";
import { parseAnnotationWorkload } from "./annotationWorkloadParsers";
import { parseDoubleAnnotationConsistency } from "./annotationRecordParsers";
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
import { ContractValidationError } from "./validation";

/** 新建批次（后端 `AnnotationBatchCreate`）。 */
export interface AnnotationBatchCreateInput {
  planned_item_count: number;
  code?: string | null;
  sequence_no?: number | null;
  name?: string | null;
  stratum?: string | null;
  item_refs?: string[];
  due_at?: string | null;
}

/** 新建分配（后端 `AnnotationAssignmentCreate`）。 */
export interface AnnotationAssignmentCreateInput {
  batch_id: string;
  assignee_id: string;
  assignment_role?: string;
  planned_item_count?: number;
  planned_minutes?: number;
  lease_expires_at?: string | null;
}

/** 分配动作可附带的量化字段（提交/完成/返工）。 */
export interface AnnotationAssignmentActionOptions
  extends AnnotationWriteOptions {
  actualMinutes?: number;
  submittedItemCount?: number;
  approvedItemCount?: number;
}

/** 周期工作量查询参数；周期两端必填（半开区间 `[start, end)`）。 */
export interface WorkloadQuery {
  assigneeId: string;
  periodStart: string;
  periodEnd: string;
}

export interface AnnotationTaskApi {
  listTasks: (
    projectId: string,
    options?: AnnotationListOptions,
  ) => Promise<CursorPage<WithAllowedActions<AnnotationTask>>>;
  createTask: (
    projectId: string,
    input: AnnotationTaskCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<AnnotationTask>>;
  getTask: (
    projectId: string,
    taskId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<AnnotationTask>>;
  getTaskConsistency: (
    projectId: string,
    taskId: string,
    options?: DetailRequestOptions,
  ) => Promise<DoubleAnnotationConsistency>;
  /** 项目级逾期汇总（只读）；服务端强制 project_id 隔离。 */
  getOverdueSummary: (
    projectId: string,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationOverdueSummary>;
  /**
   * 指派人在给定周期内的工作量聚合（只读）。
   *
   * 周期必填：没有时间窗口"工作量"没有定义。容量由服务端部署配置决定，
   * 未配置时响应里的 `capacity_minutes` / `utilization_ratio` 为 `null`。
   */
  getWorkload: (
    projectId: string,
    query: WorkloadQuery,
    options?: DetailRequestOptions,
  ) => Promise<AnnotationWorkload>;
  /**
   * 分配前的实时容量预览（只读，FR-020）。
   *
   * 选中被分配人就查，不等提交；一次请求算多人（服务端一条聚合查询）。
   * 返回的 `will_exceed` 与提交时的 409 同源，但**本层不阻断提交**——
   * 是否放行由服务端 `CapacityPolicy` 决定。
   */
  previewAssignmentCapacity: (
    projectId: string,
    request: AssignmentCapacityPreviewRequest,
    options?: DetailRequestOptions,
  ) => Promise<AssignmentCapacityPreview[]>;
  actOnTask: (
    projectId: string,
    taskId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<WithAllowedActions<AnnotationTask>>;
  listBatches: (
    projectId: string,
    taskId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<WithAllowedActions<AnnotationBatch>>>;
  getBatch: (
    projectId: string,
    batchId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<AnnotationBatch>>;
  createBatch: (
    projectId: string,
    taskId: string,
    input: AnnotationBatchCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<AnnotationBatch>>;
  actOnBatch: (
    projectId: string,
    batchId: string,
    action: string,
    options: AnnotationWriteOptions,
  ) => Promise<WithAllowedActions<AnnotationBatch>>;
  listAssignments: (
    projectId: string,
    options?: AssignmentListOptions,
  ) => Promise<CursorPage<WithAllowedActions<AnnotationAssignment>>>;
  getAssignment: (
    projectId: string,
    assignmentId: string,
    options?: DetailRequestOptions,
  ) => Promise<WithAllowedActions<AnnotationAssignment>>;
  createAssignment: (
    projectId: string,
    input: AnnotationAssignmentCreateInput,
    options?: AnnotationCreateOptions,
  ) => Promise<WithAllowedActions<AnnotationAssignment>>;
  actOnAssignment: (
    projectId: string,
    assignmentId: string,
    action: string,
    options: AnnotationAssignmentActionOptions,
  ) => Promise<WithAllowedActions<AnnotationAssignment>>;
}

function taskPath(projectId: string, taskId: string): string {
  return projectPath(
    projectId,
    `/annotation-tasks/${requireIdentifier(taskId, "task_id")}`,
  );
}

function batchPath(projectId: string, batchId: string): string {
  return projectPath(
    projectId,
    `/annotation-batches/${requireIdentifier(batchId, "batch_id")}`,
  );
}

function assignmentPath(projectId: string, assignmentId: string): string {
  return projectPath(
    projectId,
    `/annotation-assignments/${requireIdentifier(assignmentId, "assignment_id")}`,
  );
}

export function createAnnotationTaskApi(client: ApiClient): AnnotationTaskApi {
  return {
    async listTasks(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-tasks"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          overdue: options?.overdue,
          ...pageQuery(options),
        },
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
      return client.get(taskPath(projectId, taskId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseAnnotationTask(value), projectId, "标注任务"),
      });
    },

    async getTaskConsistency(projectId, taskId, options) {
      return client.get(`${taskPath(projectId, taskId)}/consistency`, {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(
            parseDoubleAnnotationConsistency(value),
            projectId,
            "双标一致性",
          ),
      });
    },

    async getOverdueSummary(projectId, options) {
      return client.get(projectPath(projectId, "/annotation-overdue-summary"), {
        signal: options?.signal,
        validate: (value) => {
          const summary = parseAnnotationOverdueSummary(value);
          if (summary.project_id !== projectId) {
            throw new ContractValidationError(
              "逾期汇总.project_id 与请求路径不一致",
            );
          }
          return summary;
        },
      });
    },

    async getWorkload(projectId, query, options) {
      return client.get(projectPath(projectId, "/annotation-workload"), {
        signal: options?.signal,
        query: {
          assignee_id: requireIdentifier(query.assigneeId, "assignee_id"),
          period_start: query.periodStart,
          period_end: query.periodEnd,
        },
        validate: (value) => {
          const workload = parseAnnotationWorkload(value);
          if (workload.project_id !== projectId) {
            throw new ContractValidationError(
              "工作量汇总.project_id 与请求路径不一致",
            );
          }
          if (workload.user_id !== query.assigneeId) {
            throw new ContractValidationError(
              "工作量汇总.user_id 与请求的 assignee_id 不一致",
            );
          }
          return workload;
        },
      });
    },

    async previewAssignmentCapacity(projectId, request, options) {
      if (request.assignee_ids.length === 0) {
        // 空列表服务端会 422；在客户端先拦是为了让调用方看见"没选人"
        // 而不是一个语义模糊的校验错误。
        return [];
      }
      return client.request(
        projectPath(projectId, "/annotation-assignment-capacity-preview"),
        {
          method: "POST",
          signal: options?.signal,
          body: {
            assignee_ids: request.assignee_ids.map((assigneeId) =>
              requireIdentifier(assigneeId, "assignee_id"),
            ),
            planned_item_count: request.planned_item_count,
            planned_minutes: request.planned_minutes,
          },
          validate: (value) => {
            const previews = parseAssignmentCapacityPreviewList(value);
            const requested = new Set(request.assignee_ids);
            for (const preview of previews) {
              if (!requested.has(preview.assignee_id)) {
                // 服务端返回了没被请求的人：口径被破坏，不能静默展示。
                throw new ContractValidationError(
                  "容量预览.assignee_id 不在请求的人员集合内",
                );
              }
            }
            return previews;
          },
        },
      );
    },

    async actOnTask(projectId, taskId, action, options) {
      return client.request(
        `${taskPath(projectId, taskId)}/actions/${requireIdentifier(action, "action")}`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          // waiver_reason 只在 complete 验收未达标时由服务端强制要求；
          // 其它动作传 null 等同于未提供，服务端按"未豁免"处理。
          body: {
            reason: options.reason ?? null,
            waiver_reason: options.waiverReason ?? null,
          },
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
      return client.get(`${taskPath(projectId, taskId)}/batches`, {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(
            parseAnnotationBatchPage(value),
            projectId,
            "标注批次列表",
          ),
      });
    },

    async getBatch(projectId, batchId, options) {
      return client.get(batchPath(projectId, batchId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(parseAnnotationBatch(value), projectId, "标注批次"),
      });
    },

    async createBatch(projectId, taskId, input, options) {
      return client.request(`${taskPath(projectId, taskId)}/batches`, {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(parseAnnotationBatch(value), projectId, "标注批次"),
      });
    },

    async actOnBatch(projectId, batchId, action, options) {
      return client.request(
        `${batchPath(projectId, batchId)}/actions/${requireIdentifier(action, "action")}`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: { reason: options.reason ?? null },
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
      return client.get(assignmentPath(projectId, assignmentId), {
        signal: options?.signal,
        validate: (value) =>
          assertProjectScope(
            parseAnnotationAssignment(value),
            projectId,
            "标注分配",
          ),
      });
    },

    async createAssignment(projectId, input, options) {
      return client.request(projectPath(projectId, "/annotation-assignments"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            parseAnnotationAssignment(value),
            projectId,
            "标注分配",
          ),
      });
    },

    async actOnAssignment(projectId, assignmentId, action, options) {
      return client.request(
        `${assignmentPath(projectId, assignmentId)}/actions/${requireIdentifier(action, "action")}`,
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options),
          body: {
            reason: options.reason ?? null,
            actual_minutes: options.actualMinutes ?? null,
            submitted_item_count: options.submittedItemCount ?? null,
            approved_item_count: options.approvedItemCount ?? null,
          },
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
