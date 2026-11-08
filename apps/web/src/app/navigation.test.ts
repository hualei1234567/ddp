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
  });

  it("未知路径返回 null，交给 404 页面处理", () => {
    expect(findNavItem("/unknown-path")).toBeNull();
  });
});
