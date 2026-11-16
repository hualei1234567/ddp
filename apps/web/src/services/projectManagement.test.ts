/**
 * 项目管理 client 的请求层测试：路径、查询参数、If-Match 与响应解析。
 * 页面的列表/空态/错误态/403 由页面测试覆盖，这里保证"路径/头写错"能被发现。
 */

import { describe, expect, it, vi } from "vitest";

import type { Project, ProjectMember } from "../types/project";
import { ApiClient } from "./api";
import { createProjectManagementApi } from "./projectManagement";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const OTHER_PROJECT_ID = "99999999-9999-7999-8999-999999999999";
const USER_ID = "22222222-2222-7222-8222-222222222222";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function client(handler: (url: string, init: RequestInit) => Response) {
  const fetchImpl = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) =>
      handler(String(input), init ?? {}),
  );
  const apiClient = new ApiClient({
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { api: createProjectManagementApi(apiClient), fetchImpl };
}

const projectBody: Project = {
  project_id: PROJECT_ID,
  code: "prj-kitchen",
  name: "厨房采集项目",
  description: null,
  status: "ACTIVE",
  version: 3,
  created_at: "2026-11-01T08:00:00Z",
  updated_at: "2026-11-01T08:00:00Z",
  membership_role: "owner",
  allowed_actions: ["read", "write", "delete", "validate", "manage_members"],
};

const memberBody: ProjectMember = {
  project_id: PROJECT_ID,
  user_id: USER_ID,
  display_name: "成员甲",
  role: "viewer",
  created_at: "2026-11-02T08:00:00Z",
  updated_at: "2026-11-02T08:00:00Z",
};

describe("projectManagementApi 请求层", () => {
  it("列表按 status/cursor/limit 查询并解析分页", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ items: [projectBody], next_cursor: "abc" }),
    );

    const page = await api.listProjects({
      status: "ARCHIVED",
      cursor: "next",
      limit: 20,
    });

    expect(page.next_cursor).toBe("abc");
    expect(page.items.map((item) => item.project_id)).toEqual([PROJECT_ID]);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/projects?cursor=next&limit=20&status=ARCHIVED");
    expect((init.headers as Record<string, string>).Accept).toBe(
      "application/json",
    );
  });

  it("创建项目使用 POST + JSON 体，创建者角色写入响应", async () => {
    const { api, fetchImpl } = client(() => jsonResponse(projectBody, 201));

    const created = await api.createProject({
      code: "prj-kitchen",
      name: "厨房采集项目",
      description: null,
    });

    expect(created.membership_role).toBe("owner");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/projects");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      code: "prj-kitchen",
      name: "厨房采集项目",
      description: null,
    });
  });

  it("PATCH 项目必须携带 If-Match 版本号", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ ...projectBody, version: 4 }),
    );

    const updated = await api.patchProject(
      PROJECT_ID,
      { name: "新名字", status: "ARCHIVED" },
      { version: 3 },
    );

    expect(updated.version).toBe(4);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/projects/${PROJECT_ID}`);
    expect(init.method).toBe("PATCH");
    expect((init.headers as Record<string, string>)["If-Match"]).toBe('"3"');
    expect(JSON.parse(String(init.body))).toEqual({
      name: "新名字",
      status: "ARCHIVED",
    });
  });

  it("成员列表按项目路径请求，并拒绝串项目的响应", async () => {
    const ok = client(() =>
      jsonResponse({ items: [memberBody], next_cursor: null }),
    );
    const page = await ok.api.listMembers(PROJECT_ID, { limit: 50 });
    expect(page.items.map((item) => item.display_name)).toEqual(["成员甲"]);
    const [url] = ok.fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/projects/${PROJECT_ID}/members?limit=50`);

    const mismatch = client(() =>
      jsonResponse({
        items: [{ ...memberBody, project_id: OTHER_PROJECT_ID }],
        next_cursor: null,
      }),
    );
    await expect(mismatch.api.listMembers(PROJECT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("添加成员、改角色、移除成员使用正确的路径与方法", async () => {
    const { api, fetchImpl } = client((_url, init) => {
      if (init.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return jsonResponse(memberBody, init.method === "POST" ? 201 : 200);
    });

    await api.addMember(PROJECT_ID, { user_id: USER_ID, role: "viewer" });
    await api.changeMemberRole(PROJECT_ID, USER_ID, "editor");
    await api.removeMember(PROJECT_ID, USER_ID);

    const addCall = fetchImpl.mock.calls[0] as [string, RequestInit];
    const roleCall = fetchImpl.mock.calls[1] as [string, RequestInit];
    const removeCall = fetchImpl.mock.calls[2] as [string, RequestInit];
    expect(addCall[0]).toBe(`/api/v1/projects/${PROJECT_ID}/members`);
    expect(addCall[1].method).toBe("POST");
    expect(roleCall[0]).toBe(
      `/api/v1/projects/${PROJECT_ID}/members/${USER_ID}`,
    );
    expect(roleCall[1].method).toBe("PATCH");
    expect(JSON.parse(String(roleCall[1].body))).toEqual({ role: "editor" });
    expect(removeCall[1].method).toBe("DELETE");
  });

  it("服务端 403 与 409 保留稳定错误码", async () => {
    const forbidden = client(() =>
      jsonResponse(
        {
          error: {
            code: "forbidden",
            message: "无权访问该项目",
            request_id: "r1",
          },
        },
        403,
      ),
    );
    await expect(forbidden.api.listMembers(PROJECT_ID)).rejects.toMatchObject({
      status: 403,
      code: "forbidden",
    });

    const conflict = client(() =>
      jsonResponse(
        { error: { code: "conflict", message: "项目至少保留一名 owner" } },
        409,
      ),
    );
    await expect(
      conflict.api.removeMember(PROJECT_ID, USER_ID),
    ).rejects.toMatchObject({ status: 409, code: "conflict" });
  });

  it("未知状态值视为契约违约，不进入 UI", async () => {
    const { api } = client(() =>
      jsonResponse({ ...projectBody, status: "DELETED" }),
    );
    await expect(api.getProject(PROJECT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});
