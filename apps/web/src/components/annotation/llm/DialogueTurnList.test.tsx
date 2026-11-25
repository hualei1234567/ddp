import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { DialogueTurn } from "../../../types/llmAnnotation";
import DialogueTurnList from "./DialogueTurnList";

const SHORT: DialogueTurn[] = [
  { role: "user", content: "帮我打开浏览器", ts: "2026-11-25T10:00:00Z" },
  { role: "assistant", content: "已打开", ts: null },
];

describe("DialogueTurnList 对话展示", () => {
  it("渲染角色徽标与短内容全文，不渲染折叠按钮", () => {
    const html = renderToStaticMarkup(<DialogueTurnList turns={SHORT} />);
    expect(html).toContain("共 2 轮");
    expect(html).toContain("user");
    expect(html).toContain("assistant");
    expect(html).toContain("帮我打开浏览器");
    expect(html).toContain("已打开");
    expect(html).toContain("2026-11-25T10:00:00Z");
    expect(html).not.toContain("展开全文");
  });

  it("长输出折叠并显示省略号与展开入口", () => {
    const long: DialogueTurn[] = [
      { role: "assistant", content: "长".repeat(300), ts: null },
    ];
    const html = renderToStaticMarkup(<DialogueTurnList turns={long} />);
    expect(html).toContain("展开全文");
    expect(html).toContain("…");
    // 折叠态不应输出完整正文（300 个"长"）
    expect(html).not.toContain("长".repeat(300));
  });

  it("未知角色回退默认徽标", () => {
    const html = renderToStaticMarkup(
      <DialogueTurnList turns={[{ role: "reviewer", content: "ok", ts: null }]} />,
    );
    expect(html).toContain("reviewer");
    expect(html).toContain("ok");
  });
});
