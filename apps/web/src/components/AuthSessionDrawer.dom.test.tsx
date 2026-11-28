import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../services/api";
import {
  clearAccessToken,
  readAccessToken,
  storeAccessToken,
} from "../services/authToken";
import type { CurrentUser, IdentityApi } from "../services/identity";
import type { Project } from "../types/project";
import AuthSessionDrawer, { type ProjectListPort } from "./AuthSessionDrawer";

const ADMIN_USER: CurrentUser = {
  user_id: "01960000-0000-7000-8000-000000000001",
  display_name: "开源管理员",
  platform_role: "admin",
};

const DEMO_PROJECT: Project = {
  project_id: "01960000-0000-7000-8000-000000000099",
  code: "OPEN-DEMO",
  name: "具身开源示范项目",
  description: "学习演示项目",
  status: "ACTIVE",
  version: 1,
  created_at: "2026-11-28T00:00:00Z",
  updated_at: "2026-11-28T00:00:00Z",
  membership_role: "owner",
  allowed_actions: ["read", "write"],
};

describe("AuthSessionDrawer 轻量登录交互", () => {
  beforeEach(() => {
    clearAccessToken();
  });

  afterEach(() => {
    clearAccessToken();
  });

  it("粘贴带 Bearer 前缀的令牌并通过 /api/v1/me 校验后完成登录", async () => {
    const user = userEvent.setup();
    const onSessionChange = vi.fn();
    const onClose = vi.fn();
    const identity: IdentityApi = {
      getCurrentUser: vi.fn().mockResolvedValue(ADMIN_USER),
    };
    const projects: ProjectListPort = {
      listProjects: vi.fn().mockResolvedValue({ items: [] }),
    };

    render(
      <AuthSessionDrawer
        open={true}
        currentProjectId={null}
        currentUser={null}
        onClose={onClose}
        onSessionChange={onSessionChange}
        onSelectProject={vi.fn()}
        identity={identity}
        projects={projects}
      />,
    );

    const textarea = screen.getByRole("textbox", { name: "访问令牌输入框" });
    await user.type(textarea, "Bearer eyJhbGciOiJIUzI1NiJ9.valid.sig");
    await user.click(screen.getByRole("button", { name: "验证并登录" }));

    await waitFor(() => {
      expect(onSessionChange).toHaveBeenCalledWith(ADMIN_USER, true);
    });
    expect(readAccessToken()).toBe("eyJhbGciOiJIUzI1NiJ9.valid.sig");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("服务端拒绝无效令牌时自动回滚，不污染会话存储", async () => {
    const user = userEvent.setup();
    const onSessionChange = vi.fn();
    const identity: IdentityApi = {
      getCurrentUser: vi.fn().mockRejectedValue(
        new ApiError({
          kind: "http",
          status: 401,
          code: "unauthorized",
          message: "无效或已过期的访问令牌",
        }),
      ),
    };

    render(
      <AuthSessionDrawer
        open={true}
        currentProjectId={null}
        currentUser={null}
        onClose={vi.fn()}
        onSessionChange={onSessionChange}
        onSelectProject={vi.fn()}
        identity={identity}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "访问令牌输入框" }),
      "bad.jwt.token",
    );
    await user.click(screen.getByRole("button", { name: "验证并登录" }));

    expect(await screen.findByText("无效或已过期的访问令牌")).toBeInTheDocument();
    expect(readAccessToken()).toBeNull();
    expect(onSessionChange).not.toHaveBeenCalled();
  });

  it("已登录态展示权威身份与项目列表，支持一键切换项目与退出登录", async () => {
    const user = userEvent.setup();
    storeAccessToken("existing.jwt.token");
    const onSelectProject = vi.fn();
    const onSessionChange = vi.fn();
    const projects: ProjectListPort = {
      listProjects: vi.fn().mockResolvedValue({ items: [DEMO_PROJECT] }),
    };

    render(
      <AuthSessionDrawer
        open={true}
        currentProjectId={null}
        currentUser={ADMIN_USER}
        onClose={vi.fn()}
        onSessionChange={onSessionChange}
        onSelectProject={onSelectProject}
        projects={projects}
      />,
    );

    expect(
      await screen.findByText("具身开源示范项目"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换项目" }));
    expect(onSelectProject).toHaveBeenCalledWith(DEMO_PROJECT.project_id);

    await user.click(
      screen.getByRole("button", { name: /退出登录/ }),
    );
    expect(readAccessToken()).toBeNull();
    expect(onSessionChange).toHaveBeenCalledWith(null, false);
  });
});
