import { describe, expect, it } from "vitest";

import {
  applyEpisodeListFilterPatch,
  buildEpisodeListPath,
  buildEpisodeListSearch,
  buildTaskDetailPath,
  buildTaskDetailSearch,
  buildTaskListSearch,
  buildWorkbenchPath,
  buildWorkbenchSearch,
  EMPTY_EPISODE_LIST_FILTERS,
  EMPTY_TASK_LIST_FILTERS,
  episodeServerFilterKey,
  hasEpisodeListFilters,
  hasTaskListFilters,
  readEpisodeListFilters,
  readTaskListFilters,
  resolveTaskDetailTab,
  resolveWorkbenchStep,
} from "./collectionUrl";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const TASK_ID = "22222222-2222-7222-8222-222222222222";
const ROBOT_ID = "33333333-3333-7333-8333-333333333333";

describe("采集任务 URL 状态", () => {
  it("筛选写入地址栏并可完整恢复", () => {
    const filters = {
      keyword: "厨房",
      status: "IN_PROGRESS",
      assignedTo: "88888888-8888-7888-8888-888888888888",
      priority: "P0",
    };
    const search = buildTaskListSearch(PROJECT_ID, filters);
    const params = new URLSearchParams(search);
    expect(readTaskListFilters(params)).toEqual(filters);
    expect(hasTaskListFilters(filters)).toBe(true);
  });

  it("空筛选不写入多余参数，project_id 始终保留", () => {
    expect(buildTaskListSearch(PROJECT_ID, EMPTY_TASK_LIST_FILTERS)).toBe(
      `?project_id=${PROJECT_ID}`,
    );
    expect(hasTaskListFilters(EMPTY_TASK_LIST_FILTERS)).toBe(false);
  });

  it("详情链接携带筛选与 Tab；默认 Tab 不写 URL", () => {
    const filters = { ...EMPTY_TASK_LIST_FILTERS, status: "REWORK" };
    expect(
      buildTaskDetailSearch(PROJECT_ID, filters, "batches"),
    ).toContain("tab=batches");
    expect(buildTaskDetailSearch(PROJECT_ID, filters, "overview")).not.toContain(
      "tab=",
    );
    expect(buildTaskDetailPath(PROJECT_ID, TASK_ID, filters, "batches")).toBe(
      `/collection/tasks/${TASK_ID}?project_id=${PROJECT_ID}&status=REWORK&tab=batches`,
    );
  });

  it("非法 Tab 与步骤回落到默认值", () => {
    expect(resolveTaskDetailTab("unknown")).toBe("overview");
    expect(resolveTaskDetailTab(null)).toBe("overview");
    expect(resolveWorkbenchStep("unknown")).toBe("confirm");
    expect(resolveWorkbenchStep("upload")).toBe("upload");
  });

  it("工作台步骤与筛选可恢复", () => {
    const filters = { ...EMPTY_TASK_LIST_FILTERS, priority: "P0" };
    expect(buildWorkbenchSearch(PROJECT_ID, "check", filters)).toBe(
      `?project_id=${PROJECT_ID}&priority=P0&step=check`,
    );
    expect(buildWorkbenchPath(PROJECT_ID, TASK_ID, "confirm", filters)).toBe(
      `/collection/workbench/${TASK_ID}?project_id=${PROJECT_ID}&priority=P0`,
    );
  });
});

describe("Episode URL 状态", () => {
  const filters = {
    keyword: "EP-0001",
    status: "READY",
    taskId: TASK_ID,
    robotId: ROBOT_ID,
    qualityStatus: "PASS",
    annotationStatus: "APPROVED",
    camera: "main",
    capturedFrom: "2026-11-09T00:00:00.000Z",
    capturedTo: "2026-11-10T00:00:00.000Z",
  };

  it("Episode 全部筛选维度写入地址栏并可完整恢复", () => {
    const search = buildEpisodeListSearch(PROJECT_ID, filters);
    expect(readEpisodeListFilters(new URLSearchParams(search))).toEqual(filters);
    expect(hasEpisodeListFilters(filters)).toBe(true);

    const params = new URLSearchParams(search);
    expect(params.get("q")).toBe("EP-0001");
    expect(params.get("status")).toBe("READY");
    expect(params.get("task_id")).toBe(TASK_ID);
    expect(params.get("robot_id")).toBe(ROBOT_ID);
    expect(params.get("quality_status")).toBe("PASS");
    expect(params.get("annotation_status")).toBe("APPROVED");
    expect(params.get("camera")).toBe("main");
    expect(params.get("captured_from")).toBe(filters.capturedFrom);
    expect(params.get("captured_to")).toBe(filters.capturedTo);

    expect(buildEpisodeListPath(PROJECT_ID, filters)).toBe(
      `/episodes${search}`,
    );
  });

  it("空 Episode 筛选不写入多余参数", () => {
    expect(
      buildEpisodeListSearch(PROJECT_ID, EMPTY_EPISODE_LIST_FILTERS),
    ).toBe(`?project_id=${PROJECT_ID}`);
    expect(hasEpisodeListFilters(EMPTY_EPISODE_LIST_FILTERS)).toBe(false);
  });

  it("时间区间一次写两个参数，空值删除且保留项目上下文", () => {
    const base = new URLSearchParams(`project_id=${PROJECT_ID}&status=READY`);
    const ranged = applyEpisodeListFilterPatch(base, {
      capturedFrom: filters.capturedFrom,
      capturedTo: filters.capturedTo,
    });
    expect(ranged.get("project_id")).toBe(PROJECT_ID);
    expect(ranged.get("status")).toBe("READY");
    expect(ranged.get("captured_from")).toBe(filters.capturedFrom);
    expect(ranged.get("captured_to")).toBe(filters.capturedTo);

    const cleared = applyEpisodeListFilterPatch(ranged, {
      capturedFrom: "",
      capturedTo: "",
    });
    expect(cleared.get("captured_from")).toBeNull();
    expect(cleared.get("captured_to")).toBeNull();
    expect(cleared.get("status")).toBe("READY");
  });

  it("服务端筛选键不含仅作用于已加载数据的关键词", () => {
    const keywordOnly = { ...EMPTY_EPISODE_LIST_FILTERS, keyword: "EP-1" };
    expect(episodeServerFilterKey(keywordOnly)).toBe(
      episodeServerFilterKey(EMPTY_EPISODE_LIST_FILTERS),
    );
    expect(
      episodeServerFilterKey({ ...keywordOnly, camera: "main" }),
    ).not.toBe(episodeServerFilterKey(keywordOnly));
  });
});
