import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AssetActionBar from "./AssetActionBar";

/** AntD 会在两个中文字符的按钮文本间插入空格（"激 活"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderBar(actions: string[], compact = false): string {
  return renderToStaticMarkup(
    <AssetActionBar
      actions={actions}
      compact={compact}
      onAction={() => Promise.resolve()}
    />,
  );
}

describe("采集素材动作条只按 allowed_actions 渲染", () => {
  it("DRAFT：服务端只给 activate 时只有激活按钮", () => {
    const html = renderBar(["activate"]);

    expect(hasButton(html, "激活")).toBe(true);
    expect(hasButton(html, "归档")).toBe(false);
  });

  it("ACTIVE：服务端只给 archive 时只有归档按钮", () => {
    const html = renderBar(["archive"]);

    expect(hasButton(html, "归档")).toBe(true);
    expect(hasButton(html, "激活")).toBe(false);
  });

  it("ARCHIVED：空数组不渲染任何动作按钮", () => {
    const html = renderBar([]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("没有可执行的动作");
  });

  it("列表内的紧凑形态用短文案表达终态", () => {
    const html = renderBar([], true);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("无动作");
  });

  it("未知动作原样展示，不猜语义", () => {
    const html = renderBar(["republish"]);

    expect(html).toContain("republish");
  });
});
