import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ActionButtons from "./ActionButtons";

function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderBar(
  actions: readonly string[],
  showFallbackNote = false,
): string {
  return renderToStaticMarkup(
    <ActionButtons
      kind="task"
      actions={actions}
      showFallbackNote={showFallbackNote}
      onAction={() => Promise.resolve()}
    />,
  );
}

describe("标注动作条只渲染传入的动作", () => {
  it("按传入集合渲染中文文案", () => {
    const html = renderBar(["start", "pause", "submit"]);

    expect(hasButton(html, "开始")).toBe(true);
    expect(hasButton(html, "暂停")).toBe(true);
    expect(hasButton(html, "提交")).toBe(true);
    expect(hasButton(html, "取消")).toBe(false);
  });

  it("空集合不渲染按钮并提示没有动作", () => {
    const html = renderBar([]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("当前状态没有可执行动作");
  });

  it("展示兜底说明时明确标注后端未下发 allowed_actions", () => {
    const html = renderBar(["start"], true);
    expect(squash(html)).toContain("当前响应未返回allowed_actions");
  });

  it("未知动作原样展示，不猜语义", () => {
    expect(renderBar(["republish"])).toContain("republish");
  });
});
