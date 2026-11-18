import { describe, expect, it } from "vitest";

import { findNavItem, navSections } from "./navigation";

describe("导航分区配置", () => {
  it("每个分区都有模块，路径唯一且以 / 开头", () => {
    const paths = navSections.flatMap((section) =>
      section.items.map((item) => item.path),
    );

    expect(navSections.length).toBeGreaterThan(0);
    for (const section of navSections) {
      expect(section.items.length).toBeGreaterThan(0);
      expect(section.label.length).toBeGreaterThan(0);
    }
    expect(new Set(paths).size).toBe(paths.length);
    expect(paths.every((path) => path.startsWith("/"))).toBe(true);
  });

  it("每个模块都有标签、说明与图标", () => {
    for (const section of navSections) {
      for (const item of section.items) {
        expect(item.label.length).toBeGreaterThan(0);
        expect(item.description.length).toBeGreaterThan(0);
        expect(item.icon).toBeTruthy();
      }
    }
  });

  it("工作台精确匹配根路径", () => {
    expect(findNavItem("/")?.item.path).toBe("/");
  });

  it("详情路径按最长前缀匹配所属模块", () => {
    expect(findNavItem("/projects/123")?.item.path).toBe("/projects");
    expect(findNavItem("/projects")?.section.key).toBe("pipeline");
    expect(findNavItem("/devices/123")?.item.path).toBe("/devices");
    expect(findNavItem("/devices")?.item.available).toBe(true);
  });

  it("标注中心主入口与工作台别名高亮同一菜单项", () => {
    expect(findNavItem("/annotation-tasks")?.item.path).toBe(
      "/annotation-tasks",
    );
    expect(findNavItem("/annotation-tasks/123")?.item.path).toBe(
      "/annotation-tasks",
    );
    expect(findNavItem("/annotation/workbench/abc")?.item.path).toBe(
      "/annotation-tasks",
    );
    expect(findNavItem("/annotation-tasks")?.item.available).toBe(true);
  });

  it("未知路径返回 null，交给 404 页面处理", () => {
    expect(findNavItem("/unknown-path")).toBeNull();
  });

  it("菜单里只有能真实进入的模块，不留占位项", () => {
    // 2026-11-18 之前的 /processing、/operations、/settings 是不可进入的
    // "待开放"占位：后端没有能力，用户点进去只能看到一句说明。产品要求
    // "不允许展示品"，所以这类项直接从菜单移除。此用例防止它们被加回来。
    const placeholders = navSections.flatMap((section) =>
      section.items.filter((item) => item.available === false),
    );
    expect(placeholders.map((item) => item.path)).toEqual([]);
  });
});

describe("质量管理导航", () => {
  // 历史缺陷：后端质量能力（黄金集 / 抽检 / IAA / 质量检查）早已实现，
  // 导航却写成 available:false，菜单里永远点不动。这里钉住"质量管理可用"
  // 以及子路径都归属同一菜单项。
  it("质量管理可用，且子路径高亮同一菜单项", () => {
    expect(findNavItem("/quality")?.item.path).toBe("/quality");
    expect(findNavItem("/quality")?.item.available).toBe(true);
    for (const path of [
      "/quality/gold-sets",
      "/quality/gold-sets/abc",
      "/quality/iaa-reports",
      "/quality/quality-samples",
      "/quality/quality-checks",
    ]) {
      expect(findNavItem(path)?.item.path).toBe("/quality");
    }
  });

  it("质量子路由都在路由表里声明", async () => {
    const { default: source } = await import("../routes.tsx?raw");

    for (const path of [
      `path="quality"`,
      `path="quality/gold-sets"`,
      `path="quality/iaa-reports"`,
      `path="quality/quality-samples"`,
      `path="quality/quality-checks"`,
    ]) {
      expect(source).toContain(path);
    }
  });
});

describe("经营分析导航", () => {
  // 历史缺陷同类回归：N9 配额与用量端点早已就绪，导航却写成 available:false，
  // 「经营分析」在菜单里是永远点不动的禁用项。这里钉住可用性与路由声明。
  it("经营分析可用，并归属平台治理分区", () => {
    const match = findNavItem("/analytics");
    expect(match?.item.path).toBe("/analytics");
    expect(match?.item.available).toBe(true);
    expect(match?.section.key).toBe("governance");
  });

  it("经营分析路由在路由表里声明", async () => {
    const { default: source } = await import("../routes.tsx?raw");
    expect(source).toContain(`path="analytics"`);
    expect(source).toContain("AnalyticsPage");
  });
});

describe("导航路径可达性", () => {
  // 真实缺陷回归守卫：此前有一个完整实现的页面（数据需求，370 行、接
  // modelOpsApi）在菜单里被写成 available:false 且路径指向根本不存在的
  // /requirements，用户永远无法从导航到达它。让"菜单指向的路径必须在路由表
  // 里存在"成为可执行断言，这类错误就不会再悄悄上线。
  it("每个启用的导航项路径都能在路由表里找到对应声明", async () => {
    // 用 Vite 的 ?raw 导入拿源码文本：vite/client 自带该类型声明，
    // 不必为测一侧导航给整个前端引入 node 类型。
    const { default: source } = await import("../routes.tsx?raw");

    for (const section of navSections) {
      for (const item of section.items) {
        if (!item.available) {
          continue;
        }
        // 根路径在路由表里以 <Route index> 声明，其余是相对写法 path="x"。
        const declared =
          item.path === "/"
            ? /<Route\s+index\b/.test(source)
            : source.includes(`path="${item.path.replace(/^\//, "")}"`);
        expect(
          declared,
          `导航项「${item.label}」指向 ${item.path}，路由表里没有对应声明`,
        ).toBe(true);
      }
    }
  });
});
