import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  episodeFixture,
  PROJECT_ID,
} from "../../services/__fixtures__/collectionFixtures";
import {
  EMPTY_EPISODE_LIST_FILTERS,
  type EpisodeListFilters,
} from "../../services/collectionUrl";
import { EpisodeFilters, EpisodeListBody } from "./EpisodeListPage";
import type { EpisodeListBodyProps } from "./EpisodeListPage";

const baseProps: EpisodeListBodyProps = {
  projectId: PROJECT_ID,
  filters: EMPTY_EPISODE_LIST_FILTERS,
  phase: "ready",
  error: null,
  items: [episodeFixture],
  filteredItems: [episodeFixture],
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  updatedAt: Date.parse("2026-11-08T05:00:00Z"),
  isMobile: false,
  selectedIds: [],
  onSelectionChange: () => undefined,
  onRetry: () => undefined,
  onResetFilters: () => undefined,
  onLoadMore: () => undefined,
};

function renderList(
  overrides: Partial<EpisodeListBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <EpisodeListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("Episode 列表页面状态", () => {
  it("403 时整页 Forbidden，不渲染 Episode 编号", () => {
    const html = renderList({
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "project_scope_denied",
        message: "非项目成员",
      }),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain(episodeFixture.code);
    expect(html).not.toContain(episodeFixture.episode_id);
  });

  it("空列表展示空态", () => {
    const html = renderList({ items: [], filteredItems: [] });
    expect(html).toContain("当前项目还没有 Episode");
  });

  it("筛选无结果与空列表文案区分", () => {
    const filters: EpisodeListFilters = {
      ...EMPTY_EPISODE_LIST_FILTERS,
      keyword: "不存在",
    };
    const html = renderList({ filters, filteredItems: [] });
    expect(html).toContain("无匹配 Episode");
    expect(html).toContain("清空筛选");
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: new ApiError({
        kind: "http",
        status: 503,
        code: "dependency_unavailable",
        message: "服务不可用",
      }),
    });
    expect(html).toContain(episodeFixture.code);
    expect(html).toContain("数据可能已过期");
  });

  it("加载失败展示错误码与重试", () => {
    const html = renderList({
      phase: "error",
      error: new ApiError({
        kind: "http",
        status: 500,
        code: "internal_error",
        message: "内部错误",
      }),
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    // antd 对两个汉字的按钮会插空格（重试 → 重 试），
    // 断言前先归一化空白，避免文案渲染细节左右用例。
    expect(html.replace(/\s+/g, "")).toContain("重试");
  });

  it("未质检 Episode 显示 Partial，READY 不冒充 ACCEPTED", () => {
    const html = renderList();
    expect(html).toContain("部分 Episode 尚未质检");
    expect(html).toContain("READY 不等于 ACCEPTED");
    expect(html).toContain("未质检");
  });

  it("已质检的 Episode 不再显示 Partial", () => {
    const checked = { ...episodeFixture, quality_status: "PASS" as const };
    const html = renderList({ items: [checked], filteredItems: [checked] });
    expect(html).not.toContain("部分 Episode 尚未质检");
    expect(html).toContain("通过");
  });

  it("移动端使用卡片、长 ID 省略并保留复制入口", () => {
    const html = renderList({ isMobile: true });
    expect(html).toContain(episodeFixture.code);
    expect(html).toContain("55555555…5555");
    expect(html).toContain("复制Episode ID");
    expect(html).toContain("查看任务");
  });

  it("详情入口指向 Episode 详情页", () => {
    const html = renderList();
    expect(html).toContain("查看详情");
    expect(html).toContain(
      `/episodes/${episodeFixture.episode_id}?project_id=${PROJECT_ID}`,
    );
  });
});

describe("Episode 筛选控件", () => {
  function renderFilters(
    filters: EpisodeListFilters = EMPTY_EPISODE_LIST_FILTERS,
    stacked = false,
  ): string {
    return renderToStaticMarkup(
      <EpisodeFilters
        filters={filters}
        loadedCount={0}
        hasMore={false}
        disabled={false}
        onChange={() => undefined}
        onReset={() => undefined}
        stacked={stacked}
      />,
    );
  }

  it("暴露全部服务端维度：状态、质量、标注、任务、机器人、机位与捕获时间", () => {
    const html = renderFilters();
    for (const label of [
      "Episode 状态（服务端筛选）",
      "质量状态（服务端筛选）",
      "标注状态（服务端筛选）",
      "采集任务 ID（服务端筛选）",
      "机器人 ID（服务端筛选）",
      "机位标识",
      "捕获开始时间",
      "捕获结束时间",
    ]) {
      expect(html).toContain(label);
    }
    // antd 对两个汉字的按钮会插空格（重置 → 重 置）。
    expect(html.replace(/\s+/g, "")).toContain("重置");
  });

  it("筛选值来自 URL 恢复的 filters，而非组件内部臆造", () => {
    const html = renderFilters({
      ...EMPTY_EPISODE_LIST_FILTERS,
      camera: "wrist",
      status: "READY",
    });
    expect(html).toContain('value="wrist"');
    expect(html).toContain("已就绪");
  });

  it("移动端筛选纵向堆叠", () => {
    const html = renderFilters(EMPTY_EPISODE_LIST_FILTERS, true);
    expect(html).toContain("ddp-filters-stacked");
  });
});
