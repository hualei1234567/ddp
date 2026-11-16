import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GOLD_SET_ACTIONS } from "../../services/qualityPresentation";
import GoldSetActionBar from "./GoldSetActionBar";

/** AntD 在两个中文字符的按钮文本间插入空格（"提 交"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderBar(actions = GOLD_SET_ACTIONS): string {
  return renderToStaticMarkup(
    <GoldSetActionBar
      actions={actions}
      onAction={() => Promise.resolve()}
    />,
  );
}

describe("黄金集动作条", () => {
  it("按传入的动作集合渲染按钮，标签来自展示映射", () => {
    const html = renderBar();

    expect(hasButton(html, "提交审核")).toBe(true);
    expect(hasButton(html, "启用")).toBe(true);
    expect(hasButton(html, "退役")).toBe(true);
    expect(hasButton(html, "撤销")).toBe(true);
  });

  it("动作集合为空时不渲染任何按钮（由页面决定传什么）", () => {
    const html = renderBar([]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("没有可用的黄金集动作");
  });

  it("只渲染传入的动作，不自行补齐缺失动作", () => {
    const html = renderBar([GOLD_SET_ACTIONS[2] ?? GOLD_SET_ACTIONS[0]!]);

    expect(hasButton(html, "启用")).toBe(true);
    expect(hasButton(html, "撤销")).toBe(false);
    expect(hasButton(html, "退役")).toBe(false);
  });
});
