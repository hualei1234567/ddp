import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { backendRobotFixture } from "../../services/__fixtures__/deviceContract.fixtures";
import { ApiError } from "../../services/api";
import { RobotListBody } from "./RobotListPage";
import type { RobotListBodyProps } from "./RobotListPage";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const robot = backendRobotFixture;

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

const baseProps: RobotListBodyProps = {
  projectId: PROJECT_ID,
  phase: "ready",
  error: null,
  items: [robot],
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  updatedAt: Date.parse("2026-11-08T12:00:00Z"),
  isMobile: false,
  onRetry: () => undefined,
  onLoadMore: () => undefined,
  onCreate: () => undefined,
  onOpenRobot: () => undefined,
};

function renderList(overrides: Partial<RobotListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <RobotListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("机器人列表页面状态", () => {
  it("403 时整页 Forbidden，不渲染机器人数据", () => {
    const html = renderList({
      phase: "forbidden",
      error: httpError(403, "无权访问"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(robot.name);
  });

  it("空列表提示先登记机器人并提供创建入口", () => {
    const html = renderList({ items: [] });

    expect(html).toContain("当前项目还没有机器人");
    expect(html).toContain("新建机器人");
  });

  it("筛选无匹配时与真正空列表区分", () => {
    const html = renderList({ items: [], hasActiveFilters: true });

    expect(html).toContain("无匹配机器人");
    expect(html).not.toContain("当前项目还没有机器人");
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain(robot.name);
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的机器人数据");
  });

  it("表格展示机器人并给出详情入口", () => {
    const html = renderList();

    expect(html).toContain(robot.name);
    expect(html).toContain(robot.code);
    expect(html).toContain("查看详情");
    expect(html).toContain("ant-table");
  });

  it("窄屏使用卡片而不是横向表格", () => {
    const html = renderList({ isMobile: true });

    expect(html).toContain(robot.name);
    expect(html).toContain("ant-card");
    expect(html).not.toContain("ant-table");
  });
});
