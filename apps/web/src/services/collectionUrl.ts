import { DEFAULT_WORKBENCH_STEP } from "./workbenchChecklist";

/**
 * 阶段 2 页面的 URL 状态。
 *
 * 只读写白名单参数：筛选、详情 Tab、工作台步骤均可通过地址栏恢复。
 * 服务端不支持的筛选（关键词、优先级）只作用于已加载数据，页面必须标注范围。
 */

export interface TaskListFilters {
  keyword: string;
  status: string;
  assignedTo: string;
  priority: string;
}

export interface EpisodeListFilters {
  keyword: string;
  status: string;
  taskId: string;
}

export const EMPTY_TASK_LIST_FILTERS: TaskListFilters = {
  keyword: "",
  status: "",
  assignedTo: "",
  priority: "",
};

export const EMPTY_EPISODE_LIST_FILTERS: EpisodeListFilters = {
  keyword: "",
  status: "",
  taskId: "",
};

export const TASK_DETAIL_TAB_VALUES = [
  "overview",
  "sop",
  "batches",
  "uploads",
  "operations",
] as const;

export type TaskDetailTab = (typeof TASK_DETAIL_TAB_VALUES)[number];
export const DEFAULT_TASK_DETAIL_TAB: TaskDetailTab = "overview";

export const WORKBENCH_STEP_VALUES = [
  "confirm",
  "check",
  "capture",
  "upload",
  "submit",
] as const;

export type WorkbenchStep = (typeof WORKBENCH_STEP_VALUES)[number];

function readParam(params: URLSearchParams, key: string): string {
  return (params.get(key) ?? "").trim();
}

export function readTaskListFilters(params: URLSearchParams): TaskListFilters {
  return {
    keyword: readParam(params, "q"),
    status: readParam(params, "status"),
    assignedTo: readParam(params, "assigned_to"),
    priority: readParam(params, "priority"),
  };
}

export function hasTaskListFilters(filters: TaskListFilters): boolean {
  return (
    filters.keyword !== "" ||
    filters.status !== "" ||
    filters.assignedTo !== "" ||
    filters.priority !== ""
  );
}

export function readEpisodeListFilters(
  params: URLSearchParams,
): EpisodeListFilters {
  return {
    keyword: readParam(params, "q"),
    status: readParam(params, "status"),
    taskId: readParam(params, "task_id"),
  };
}

export function hasEpisodeListFilters(filters: EpisodeListFilters): boolean {
  return (
    filters.keyword !== "" || filters.status !== "" || filters.taskId !== ""
  );
}

export function resolveTaskDetailTab(
  value: string | null | undefined,
): TaskDetailTab {
  if (!value) {
    return DEFAULT_TASK_DETAIL_TAB;
  }
  return (TASK_DETAIL_TAB_VALUES as readonly string[]).includes(value)
    ? (value as TaskDetailTab)
    : DEFAULT_TASK_DETAIL_TAB;
}

export function resolveWorkbenchStep(
  value: string | null | undefined,
): WorkbenchStep {
  if (!value) {
    return DEFAULT_WORKBENCH_STEP;
  }
  return (WORKBENCH_STEP_VALUES as readonly string[]).includes(value)
    ? (value as WorkbenchStep)
    : DEFAULT_WORKBENCH_STEP;
}

function buildSearch(
  projectId: string | null,
  entries: [string, string][],
): string {
  const params = new URLSearchParams();
  if (projectId) {
    params.set("project_id", projectId);
  }
  for (const [key, value] of entries) {
    if (value.trim() !== "") {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function taskFilterEntries(filters: TaskListFilters): [string, string][] {
  return [
    ["q", filters.keyword],
    ["status", filters.status],
    ["assigned_to", filters.assignedTo],
    ["priority", filters.priority],
  ];
}

export function buildTaskListSearch(
  projectId: string | null,
  filters: TaskListFilters = EMPTY_TASK_LIST_FILTERS,
): string {
  return buildSearch(projectId, taskFilterEntries(filters));
}

export function buildTaskDetailSearch(
  projectId: string | null,
  filters: TaskListFilters = EMPTY_TASK_LIST_FILTERS,
  tab?: TaskDetailTab,
): string {
  return buildSearch(projectId, [
    ...taskFilterEntries(filters),
    ["tab", tab && tab !== DEFAULT_TASK_DETAIL_TAB ? tab : ""],
  ]);
}

export function buildTaskDetailPath(
  projectId: string | null,
  taskId: string,
  filters: TaskListFilters = EMPTY_TASK_LIST_FILTERS,
  tab?: TaskDetailTab,
): string {
  return `/collection/tasks/${encodeURIComponent(taskId)}${buildTaskDetailSearch(
    projectId,
    filters,
    tab,
  )}`;
}

export function buildWorkbenchSearch(
  projectId: string | null,
  step?: WorkbenchStep,
  filters: TaskListFilters = EMPTY_TASK_LIST_FILTERS,
): string {
  return buildSearch(projectId, [
    ...taskFilterEntries(filters),
    ["step", step && step !== DEFAULT_WORKBENCH_STEP ? step : ""],
  ]);
}

export function buildWorkbenchPath(
  projectId: string | null,
  taskId: string,
  step?: WorkbenchStep,
  filters: TaskListFilters = EMPTY_TASK_LIST_FILTERS,
): string {
  return `/collection/workbench/${encodeURIComponent(taskId)}${buildWorkbenchSearch(
    projectId,
    step,
    filters,
  )}`;
}

export function buildEpisodeListSearch(
  projectId: string | null,
  filters: EpisodeListFilters = EMPTY_EPISODE_LIST_FILTERS,
): string {
  return buildSearch(projectId, [
    ["q", filters.keyword],
    ["status", filters.status],
    ["task_id", filters.taskId],
  ]);
}

export function buildEpisodeListPath(
  projectId: string | null,
  filters: EpisodeListFilters = EMPTY_EPISODE_LIST_FILTERS,
): string {
  return `/episodes${buildEpisodeListSearch(projectId, filters)}`;
}
