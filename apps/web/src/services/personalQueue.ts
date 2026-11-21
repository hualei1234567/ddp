/**
 * 个人领取队列的聚合读取。
 *
 * 为什么在客户端聚合：后端没有跨项目的「我的分配」端点，分配按
 * `/projects/{project_id}/annotation-assignments?assignee_id=` 查询；而
 * 「我是谁」只能由服务端回答（`GET /api/v1/me`）。本模块把两者串起来，
 * 并把**部分失败**如实上报——某个项目读取失败时，其余项目的队列照常展示，
 * 不把失败隐藏成"没有任务"。
 *
 * 边界：项目数量与每项目页数都有上限；触顶时 `truncated=true`，绝不把
 * 截断结果伪装成完整队列。
 */

import type { CursorPage } from "../types/api";
import type { Project } from "../types/project";
import type { AnnotationAssignment } from "../types/annotation";
import type { WithAllowedActions } from "./annotationAllowedActions";
import { ApiError } from "./api";
import { CursorPageOverflowError, fetchAllCursorPages } from "./cursorPage";

/** 某一段读取失败的作用域与原因；页面逐条展示，不合并成"加载失败"。 */
export interface PersonalQueueFailure {
  /** `identity` / `projects` / `project:<code>`。 */
  scope: string;
  error: ApiError;
}

/** 个人队列视为「待处理」的分配状态。 */
export const QUEUE_OPEN_STATUSES = ["ASSIGNED", "CLAIMED", "IN_PROGRESS"] as const;
export type QueueOpenStatus = (typeof QUEUE_OPEN_STATUSES)[number];

export interface QueueEntry {
  projectId: string;
  projectCode: string;
  projectName: string;
  assignment: AnnotationAssignment;
  /** 服务端给出的可执行动作；前端只按此渲染按钮。 */
  allowedActions: readonly string[];
}

export interface PersonalQueue {
  /** 待处理（待领取 / 已领取 / 进行中）分配。 */
  open: QueueEntry[];
  /** 已提交、等待复核的分配，供标注人回看进度。 */
  submitted: QueueEntry[];
  failures: PersonalQueueFailure[];
  /** 项目数或页数触顶：队列不完整。 */
  truncated: boolean;
  scannedProjects: number;
}

type AssignmentPage = CursorPage<WithAllowedActions<AnnotationAssignment>>;

export interface PersonalQueueDeps {
  identity: {
    getCurrentUser: (options?: { signal?: AbortSignal }) => Promise<{
      user_id: string;
    }>;
  };
  projects: {
    listProjects: (options?: {
      signal?: AbortSignal;
      limit?: number;
      cursor?: string;
    }) => Promise<CursorPage<Project>>;
  };
  assignments: {
    listAssignments: (
      projectId: string,
      options?: {
        signal?: AbortSignal;
        assigneeId?: string;
        status?: string;
        limit?: number;
        cursor?: string;
      },
    ) => Promise<AssignmentPage>;
  };
}

/** 单个用户最多扫描的项目数；超出即截断并显式标记。 */
export const MAX_SCANNED_PROJECTS = 20;
/** 单个项目最多翻页数；超出同样标记截断。 */
const MAX_PAGES_PER_SOURCE = 5;

function toEntry(
  project: Project,
  assignment: WithAllowedActions<AnnotationAssignment>,
): QueueEntry {
  return {
    projectId: project.project_id,
    projectCode: project.code,
    projectName: project.name,
    assignment,
    allowedActions: assignment.allowed_actions ?? [],
  };
}

/**
 * 读取当前用户的个人队列。
 *
 * 失败策略：
 * - 身份读取失败 → 整页失败。没有身份就不可能有"我的"队列，猜一个只会串号。
 * - 项目列表失败 → 无法定位队列，返回空集合 + `projects` 失败项。
 * - 单个项目失败 → 记入 `failures` 并继续，其余项目照常展示。
 */
export async function loadPersonalQueue(
  deps: PersonalQueueDeps,
  signal: AbortSignal,
  options: { maxProjects?: number } = {},
): Promise<PersonalQueue> {
  const maxProjects = options.maxProjects ?? MAX_SCANNED_PROJECTS;
  const failures: PersonalQueueFailure[] = [];

  const user = await deps.identity.getCurrentUser({ signal });

  let projects: Project[];
  try {
    projects = await fetchAllCursorPages(
      (params) =>
        deps.projects.listProjects({
          signal,
          limit: params.limit,
          ...(params.cursor === undefined ? {} : { cursor: params.cursor }),
        }),
      (item: Project) => item.project_id,
      { maxPages: MAX_PAGES_PER_SOURCE },
    );
  } catch (caught) {
    return {
      open: [],
      submitted: [],
      failures: [{ scope: "projects", error: asQueueError(caught) }],
      truncated: false,
      scannedProjects: 0,
    };
  }

  const truncatedProjects = projects.length > maxProjects;
  const scanned = projects.slice(0, maxProjects);
  const open: QueueEntry[] = [];
  const submitted: QueueEntry[] = [];
  let truncatedPages = false;

  for (const project of scanned) {
    try {
      const assignments = await fetchAllCursorPages(
        (params) =>
          deps.assignments.listAssignments(project.project_id, {
            signal,
            assigneeId: user.user_id,
            limit: params.limit,
            ...(params.cursor === undefined ? {} : { cursor: params.cursor }),
          }),
        (item) => item.assignment_id,
        { maxPages: MAX_PAGES_PER_SOURCE },
      );
      for (const assignment of assignments) {
        if (QUEUE_OPEN_STATUSES.includes(assignment.status as QueueOpenStatus)) {
          open.push(toEntry(project, assignment));
        } else if (assignment.status === "SUBMITTED") {
          submitted.push(toEntry(project, assignment));
        }
        // COMPLETED / REVOKED / EXPIRED 不属于「我的待办」，不进队列。
      }
    } catch (caught) {
      if (caught instanceof CursorPageOverflowError) {
        truncatedPages = true;
      }
      failures.push({
        scope: `project:${project.code}`,
        error: asQueueError(caught),
      });
    }
  }

  const byAssignmentId = (left: QueueEntry, right: QueueEntry) =>
    left.assignment.assignment_id.localeCompare(right.assignment.assignment_id);

  return {
    open: [...open].sort(byAssignmentId),
    submitted: [...submitted].sort(byAssignmentId),
    failures,
    truncated: truncatedProjects || truncatedPages,
    scannedProjects: scanned.length,
  };
}

/** 把未知异常收敛为 ApiError 形态，页面统一按错误码分支。 */
function asQueueError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({
        kind: "network",
        message: "读取个人队列失败",
        cause: caught,
      });
}
