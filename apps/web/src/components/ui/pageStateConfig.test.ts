import { describe, expect, it } from "vitest";

import {
  PAGE_STATE_CONTENT,
  type PageStateVariant,
} from "./pageStateConfig";

const variants: PageStateVariant[] = [
  "loading",
  "empty",
  "error",
  "forbidden",
  "partial",
  "stale",
  "disabled",
  "pending",
  "success",
  "failed",
];

describe("页面状态文案", () => {
  it("覆盖规范要求的全部页面状态且文案非空", () => {
    for (const variant of variants) {
      const content = PAGE_STATE_CONTENT[variant];
      expect(content.title.length).toBeGreaterThan(0);
      expect(content.description.length).toBeGreaterThan(0);
    }
  });

  it("错误与空状态有可识别的标题", () => {
    expect(PAGE_STATE_CONTENT.error.title).toContain("失败");
    expect(PAGE_STATE_CONTENT.empty.title).toContain("暂无");
    expect(PAGE_STATE_CONTENT.forbidden.title).toContain("无权");
  });

  it("部分失败与过期数据使用警告色调", () => {
    expect(PAGE_STATE_CONTENT.partial.tone).toBe("warning");
    expect(PAGE_STATE_CONTENT.stale.tone).toBe("warning");
  });
});
