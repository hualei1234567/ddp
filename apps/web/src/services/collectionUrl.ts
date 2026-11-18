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
  robotId: string;
  qualityStatus: string;
  annotationStatus: string;
  camera: string;
  capturedFrom: string;
  capturedTo: string;
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
  robotId: "",
  qualityStatus: "",
  annotationStatus: "",
  camera: "",
  capturedFrom: "",
  capturedTo: "",
};

/**
 * Episode 筛选字段 → URL 参数的唯一映射。
 *
 * 所有维度都必须写进地址栏：页面可分享、刷新与浏览器回退都能恢复现场。
 * 关键词只在已加载数据上生效，其余维度由服务端下推执行。
 */
const EPISODE_FILTER_PARAM_KEYS: Record<keyof EpisodeListFilters, string> = {
  keyword: "q",
  status: "status",
  taskId: "task_id",
  robotId: "robot_id",
  qualityStatus: "quality_status",
  annotationStatus: "annotation_status",
  camera: "camera",
  capturedFrom: "captured_from",
  capturedTo: "captured_to",
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
    robotId: readParam(params, "robot_id"),
    qualityStatus: readParam(params, "quality_status"),
    annotationStatus: readParam(params, "annotation_status"),
    camera: readParam(params, "camera"),
    capturedFrom: readParam(params, "captured_from"),
    capturedTo: readParam(params, "captured_to"),
  };
}

export function hasEpisodeListFilters(filters: EpisodeListFilters): boolean {
  return Object.values(filters).some((value) => value !== "");
}

/** 服务端筛选维度；任一变化都必须重新从第一页加载。 */
export function episodeServerFilterKey(filters: EpisodeListFilters): string {
  return [
    filters.status,
    filters.taskId,
    filters.robotId,
    filters.qualityStatus,
    filters.annotationStatus,
    filters.camera,
    filters.capturedFrom,
    filters.capturedTo,
  ].join("|");
}

/**
 * 把一组筛选变更写进查询参数：空值删除参数，其余参数保持不变。
 *
 * 使用补丁而不是逐字段回调，是为了让时间区间这类一次改两个字段的操作
 * 只产生一次导航，避免第二次写入用过期的查询串覆盖第一次。
 */
export function applyEpisodeListFilterPatch(
  params: URLSearchParams,
  patch: Partial<EpisodeListFilters>,
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of Object.keys(patch) as (keyof EpisodeListFilters)[]) {
    const paramKey = EPISODE_FILTER_PARAM_KEYS[key];
    const value = patch[key];
    if (value) {
      next.set(paramKey, value);
    } else {
      next.delete(paramKey);
    }
  }
  return next;
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
  const entries = (Object.keys(EPISODE_FILTER_PARAM_KEYS) as (
    | keyof EpisodeListFilters
  )[]).map(
    (key): [string, string] => [EPISODE_FILTER_PARAM_KEYS[key], filters[key]],
  );
  return buildSearch(projectId, entries);
}

export function buildEpisodeListPath(
  projectId: string | null,
  filters: EpisodeListFilters = EMPTY_EPISODE_LIST_FILTERS,
): string {
  return `/episodes${buildEpisodeListSearch(projectId, filters)}`;
}

/** 详情页保留列表筛选与项目上下文，便于返回时恢复现场。 */
export function buildEpisodeDetailPath(
  projectId: string | null,
  episodeId: string,
  filters: EpisodeListFilters = EMPTY_EPISODE_LIST_FILTERS,
): string {
  return `/episodes/${encodeURIComponent(episodeId)}${buildEpisodeListSearch(
    projectId,
    filters,
  )}`;
}

export function buildRawAssetDetailPath(
  projectId: string | null,
  assetId: string,
): string {
  return `/raw-assets/${encodeURIComponent(assetId)}${buildSearch(projectId, [])}`;
}
