import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  collectionTaskFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/collectionFixtures";
import {
  EMPTY_TASK_LIST_FILTERS,
  type TaskListFilters,
} from "../../services/collectionUrl";
import { CollectionTaskListBody } from "./CollectionTaskListPage";
import type { CollectionTaskListBodyProps } from "./CollectionTaskListPage";

function httpError(status: number, code: string, message: string): ApiError {
  return new ApiError({ kind: "http", status, code, message });
}

const baseProps: CollectionTaskListBodyProps = {
  projectId: PROJECT_ID,
  filters: EMPTY_TASK_LIST_FILTERS,
  phase: "ready",
  error: null,
  items: [collectionTaskFixture],
  filteredItems: [collectionTaskFixture],
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  updatedAt: Date.parse("2026-11-08T04:00:00Z"),
  isMobile: false,
  onRetry: () => undefined,
  onResetFilters: () => undefined,
  onLoadMore: () => undefined,
};

function renderList(
  overrides: Partial<CollectionTaskListBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <CollectionTaskListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("采集任务列表页面状态", () => {
  it("403 时整页 Forbidden，不渲染任务名称或 ID", () => {
    const html = renderList({
      phase: "forbidden",
      error: httpError(403, "project_scope_denied", "非项目成员"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(collectionTaskFixture.name);
    expect(html).not.toContain(collectionTaskFixture.task_id);
  });

  it("首次加载成功但无任务时展示空态", () => {
    const html = renderList({ items: [], filteredItems: [] });
    expect(html).toContain("当前项目还没有采集任务");
    expect(html).toContain("新建任务");
  });

  it("有任务但筛选无匹配时展示无匹配空态", () => {
    const filters: TaskListFilters = {
      ...EMPTY_TASK_LIST_FILTERS,
      keyword: "不存在",
    };
    const html = renderList({ filters, filteredItems: [] });
    expect(html).toContain("无匹配任务");
    expect(html).toContain("清空筛选");
  });

  it("刷新失败时保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(503, "dependency_unavailable", "服务暂时不可用"),
    });

    expect(html).toContain(collectionTaskFixture.name);
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的任务数据");
  });

  it("加载失败展示错误码与重试", () => {
    const html = renderList({
      phase: "error",
      error: httpError(500, "internal_error", "内部错误"),
    });

    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    // antd 对两个汉字的按钮会插空格（重试 → 重 试），
    // 断言前先归一化空白，避免文案渲染细节左右用例。
    expect(html.replace(/\s+/g, "")).toContain("重试");
  });

  it("移动端使用卡片，长 ID 中间省略并提供复制入口", () => {
    const html = renderList({ isMobile: true });

    expect(html).toContain(collectionTaskFixture.name);
    expect(html).toContain("22222222…2222");
    expect(html).toContain("复制任务 ID");
    expect(html).toContain("进入现场工作台");
    expect(html).toContain(
      `/collection/workbench/${collectionTaskFixture.task_id}?project_id=${PROJECT_ID}`,
    );
  });

  it("进度目标为 0 时显示 N/A 而不是伪造 0%", () => {
    const zeroTarget = {
      ...collectionTaskFixture,
      target_episode_count: 0,
      submitted_episode_count: 0,
      accepted_episode_count: 0,
    };
    const html = renderList({
      items: [zeroTarget],
      filteredItems: [zeroTarget],
      isMobile: true,
    });
    expect(html).toContain("N/A");
  });
});
