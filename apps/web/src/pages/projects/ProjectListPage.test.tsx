import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type { Project } from "../../types/project";
import { ProjectListBody, type ProjectListBodyProps } from "./ProjectListPage";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";

const projectFixture: Project = {
  project_id: PROJECT_ID,
  code: "prj-kitchen",
  name: "厨房采集项目",
  description: "厨房场景数据采集",
  status: "ACTIVE",
  version: 3,
  created_at: "2026-11-01T08:00:00Z",
  updated_at: "2026-11-10T08:00:00Z",
  membership_role: "owner",
  allowed_actions: ["read", "write", "delete", "validate", "manage_members"],
};

const readOnlyFixture: Project = {
  ...projectFixture,
  project_id: "22222222-2222-7222-8222-222222222222",
  code: "prj-view",
  name: "只读项目",
  membership_role: "viewer",
  allowed_actions: ["read"],
};

function httpError(status: number, code: string, message: string): ApiError {
  return new ApiError({ kind: "http", status, code, message });
}

const baseProps: ProjectListBodyProps = {
  phase: "ready",
  error: null,
  items: [projectFixture],
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  statusFilter: "ACTIVE",
  onStatusFilterChange: () => undefined,
  onCreate: () => undefined,
  onEnter: () => undefined,
  onEdit: () => undefined,
  onRetry: () => undefined,
  onRefresh: () => undefined,
  onLoadMore: () => undefined,
};

function renderBody(overrides: Partial<ProjectListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ProjectListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("项目管理列表状态", () => {
  it("403 时整页 Forbidden，不渲染项目名称或编码", () => {
    const html = renderBody({
      phase: "forbidden",
      error: httpError(403, "forbidden", "无权访问"),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain(projectFixture.name);
    expect(html).not.toContain(projectFixture.code);
  });

  it("没有任何可见项目时展示空态并说明不会自动创建", () => {
    const html = renderBody({ items: [] });
    expect(html).toContain("还没有可见的项目");
    expect(html).toContain("不会自动生成或伪造默认项目");
    expect(html).toContain("创建项目");
  });

  it("归档筛选下的空态说明归档不删除数据", () => {
    const html = renderBody({ items: [], statusFilter: "ARCHIVED" });
    expect(html).toContain("没有已归档的项目");
    expect(html).toContain("不会删除数据");
  });

  it("列表展示编码、角色与进入项目入口", () => {
    const html = renderBody();
    expect(html).toContain(projectFixture.name);
    expect(html).toContain(projectFixture.code);
    expect(html).toContain("负责人");
    expect(html).toContain("进入项目");
    expect(html).toContain("设置");
    expect(html).toContain("创建项目");
  });

  it("只读项目不渲染设置入口", () => {
    const html = renderBody({ items: [readOnlyFixture] });
    expect(html).toContain(readOnlyFixture.name);
    expect(html).toContain("只读");
    expect(html).not.toContain(">设置<");
  });

  it("平台管理员（无成员角色）显示管理员标签", () => {
    const html = renderBody({
      items: [
        {
          ...projectFixture,
          membership_role: null,
          allowed_actions: [
            "read",
            "write",
            "delete",
            "validate",
            "manage_members",
          ],
        },
      ],
    });
    expect(html).toContain("平台管理员");
  });

  it("首次加载失败展示错误码与重试", () => {
    const html = renderBody({
      phase: "error",
      items: [],
      error: httpError(500, "internal_error", "内部错误"),
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    expect(html.replace(/\s+/g, "")).toContain("重试");
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderBody({
      stale: true,
      error: httpError(503, "dependency_unavailable", "服务暂时不可用"),
    });
    expect(html).toContain(projectFixture.name);
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的项目列表");
  });

  it("始终说明不提供删除项目并引导归档", () => {
    const html = renderBody();
    expect(html).toContain("不提供删除项目");
    expect(html).toContain("已归档");
  });
});
