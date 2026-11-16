import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type { Project, ProjectMember } from "../../types/project";
import { PeopleBody, type PeopleBodyProps } from "./PeopleBody";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const OWNER_ID = "22222222-2222-7222-8222-222222222222";
const VIEWER_ID = "33333333-3333-7333-8333-333333333333";

const ownerProject: Project = {
  project_id: PROJECT_ID,
  code: "prj-kitchen",
  name: "厨房采集项目",
  description: null,
  status: "ACTIVE",
  version: 1,
  created_at: "2026-11-01T08:00:00Z",
  updated_at: "2026-11-01T08:00:00Z",
  membership_role: "owner",
  allowed_actions: ["read", "write", "delete", "validate", "manage_members"],
};

const readOnlyProject: Project = {
  ...ownerProject,
  membership_role: "viewer",
  allowed_actions: ["read"],
};

const archivedProject: Project = {
  ...ownerProject,
  status: "ARCHIVED",
};

const members: ProjectMember[] = [
  {
    project_id: PROJECT_ID,
    user_id: OWNER_ID,
    display_name: "成员甲",
    role: "owner",
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-01T08:00:00Z",
  },
  {
    project_id: PROJECT_ID,
    user_id: VIEWER_ID,
    display_name: "成员乙",
    role: "viewer",
    created_at: "2026-11-02T08:00:00Z",
    updated_at: "2026-11-02T08:00:00Z",
  },
];

function httpError(status: number, code: string, message: string): ApiError {
  return new ApiError({ kind: "http", status, code, message });
}

const baseProps: PeopleBodyProps = {
  projects: [ownerProject],
  projectsLoading: false,
  projectsError: null,
  projectsTruncated: false,
  selectedProject: ownerProject,
  phase: "ready",
  error: null,
  members,
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  onSelectProject: () => undefined,
  onAddMember: () => undefined,
  onChangeRole: () => undefined,
  onRemove: () => undefined,
  onRetry: () => undefined,
  onRefresh: () => undefined,
  onLoadMore: () => undefined,
};

function renderBody(overrides: Partial<PeopleBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <PeopleBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("人员与团队页面状态", () => {
  it("403 时整页 Forbidden，不渲染成员姓名与 ID", () => {
    const html = renderBody({
      phase: "forbidden",
      members: [],
      error: httpError(403, "forbidden", "无权访问该项目"),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain("成员甲");
    expect(html).not.toContain(OWNER_ID);
  });

  it("没有项目时提示先在项目管理中创建或选择", () => {
    const html = renderBody({
      projects: [],
      selectedProject: null,
      members: [],
    });
    expect(html).toContain("未选择项目");
    expect(html).toContain("前往项目管理");
  });

  it("负责人看到成员、角色与移除入口", () => {
    const html = renderBody();
    expect(html).toContain("成员甲");
    expect(html).toContain("成员乙");
    expect(html).toContain("你可以管理本项目成员");
    expect(html).toContain("移除");
    expect(html).toContain(OWNER_ID);
  });

  it("只读角色看不到管理入口并明确说明前端提示不等于授权", () => {
    const html = renderBody({
      projects: [readOnlyProject],
      selectedProject: readOnlyProject,
    });
    expect(html).toContain("当前角色只读");
    expect(html).toContain("服务端仍会对每个写请求做权限校验");
    expect(html).not.toContain(">移除<");
    // 添加成员按钮存在但禁用。
    expect(html).toMatch(/添加成员/);
    expect(html).toMatch(/disabled/);
  });

  it("成员为空时展示空态而不是伪造成员", () => {
    const html = renderBody({ members: [] });
    expect(html).toContain("该项目还没有成员");
    expect(html).toContain("不会自动填充成员");
  });

  it("项目加载失败展示错误态", () => {
    const html = renderBody({
      projects: [],
      selectedProject: null,
      projectsError: httpError(503, "dependency_unavailable", "服务暂时不可用"),
      members: [],
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("dependency_unavailable");
  });

  it("归档项目给出提示但保留成员展示", () => {
    const html = renderBody({
      projects: [archivedProject],
      selectedProject: archivedProject,
    });
    expect(html).toContain("该项目已归档");
    expect(html).toContain("不会删除数据");
    expect(html).toContain("成员甲");
  });

  it("刷新失败保留旧成员并提示 Stale", () => {
    const html = renderBody({
      stale: true,
      error: httpError(503, "dependency_unavailable", "服务暂时不可用"),
    });
    expect(html).toContain("成员甲");
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的成员列表");
  });
});
