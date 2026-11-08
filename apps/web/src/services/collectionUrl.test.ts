import { describe, expect, it } from "vitest";

import {
  buildEpisodeListPath,
  buildEpisodeListSearch,
  buildTaskDetailPath,
  buildTaskDetailSearch,
  buildTaskListSearch,
  buildWorkbenchPath,
  buildWorkbenchSearch,
  EMPTY_EPISODE_LIST_FILTERS,
  EMPTY_TASK_LIST_FILTERS,
  hasEpisodeListFilters,
  hasTaskListFilters,
  readEpisodeListFilters,
  readTaskListFilters,
  resolveTaskDetailTab,
  resolveWorkbenchStep,
} from "./collectionUrl";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const TASK_ID = "22222222-2222-7222-8222-222222222222";

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
  it("Episode 筛选写入地址栏并可恢复", () => {
    const filters = {
      keyword: "EP-0001",
      status: "READY",
      taskId: TASK_ID,
    };
    expect(readEpisodeListFilters(new URLSearchParams(
      buildEpisodeListSearch(PROJECT_ID, filters),
    ))).toEqual(filters);
    expect(hasEpisodeListFilters(filters)).toBe(true);
    expect(buildEpisodeListPath(PROJECT_ID, filters)).toBe(
      `/episodes?project_id=${PROJECT_ID}&q=EP-0001&status=READY&task_id=${TASK_ID}`,
    );
  });

  it("空 Episode 筛选不写入多余参数", () => {
    expect(
      buildEpisodeListSearch(PROJECT_ID, EMPTY_EPISODE_LIST_FILTERS),
    ).toBe(`?project_id=${PROJECT_ID}`);
    expect(hasEpisodeListFilters(EMPTY_EPISODE_LIST_FILTERS)).toBe(false);
  });
});
