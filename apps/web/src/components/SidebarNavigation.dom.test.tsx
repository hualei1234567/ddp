import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SidebarNavigation from "./SidebarNavigation";

describe("SidebarNavigation 可折叠分组与检索交互", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("支持点击分组标题折叠与展开「资源与运营」及「平台治理」", async () => {
    const user = userEvent.setup();
    render(
      <SidebarNavigation
        selectedKeys={["/"]}
        activeSectionKey="overview"
        onNavigate={vi.fn()}
      />,
    );

    expect(screen.getByText("设备纳管")).toBeInTheDocument();
    expect(screen.getByText("审计日志")).toBeInTheDocument();

    // 点击「资源与运营」分组头折叠
    await user.click(screen.getByText("资源与运营"));
    await waitFor(() => {
      const stored = JSON.parse(
        window.localStorage.getItem("ddp.nav.open_keys") ?? "[]",
      ) as string[];
      expect(stored).not.toContain("section:resources");
      expect(stored).toContain("section:governance");
    });

    // 再次点击重新展开
    await user.click(screen.getByText("资源与运营"));
    await waitFor(() => {
      const stored = JSON.parse(
        window.localStorage.getItem("ddp.nav.open_keys") ?? "[]",
      ) as string[];
      expect(stored).toContain("section:resources");
    });
  });

  it("支持一键全部收起与全部展开分组", async () => {
    const user = userEvent.setup();
    render(
      <SidebarNavigation
        selectedKeys={["/"]}
        activeSectionKey="overview"
        onNavigate={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "全部收起分组" }));
    expect(
      JSON.parse(window.localStorage.getItem("ddp.nav.open_keys") ?? "[]"),
    ).toEqual([]);

    await user.click(screen.getByRole("button", { name: "全部展开分组" }));
    const restored = JSON.parse(
      window.localStorage.getItem("ddp.nav.open_keys") ?? "[]",
    ) as string[];
    expect(restored).toEqual(
      expect.arrayContaining([
        "section:plan",
        "section:process",
        "section:annotate",
        "section:consume",
        "section:resources",
        "section:governance",
      ]),
    );
  });

  it("支持关键字实时过滤菜单项并点击跳转", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(
      <SidebarNavigation
        selectedKeys={["/"]}
        activeSectionKey="overview"
        onNavigate={onNavigate}
      />,
    );

    const searchInput = screen.getByRole("textbox", {
      name: "检索左侧导航菜单",
    });
    await user.type(searchInput, "审计日志");

    expect(screen.getByText("审计日志")).toBeInTheDocument();
    expect(screen.queryByText("设备纳管")).toBeNull();

    await user.click(screen.getByText("审计日志"));
    expect(onNavigate).toHaveBeenCalledWith("/audit-logs");
  });
});
