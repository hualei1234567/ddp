import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import BatchActionBar from "./BatchActionBar";

/** AntD 在两个中文字符的按钮文本间插空格（"暂 停"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderBar(allowedActions: string[] | undefined): string {
  return renderToStaticMarkup(
    <BatchActionBar
      allowedActions={allowedActions}
      onAction={() => Promise.resolve()}
    />,
  );
}

describe("采集批次动作条只按 allowed_actions 渲染", () => {
  it("只渲染服务端下发的动作", () => {
    const html = renderBar(["pause", "submit"]);

    expect(hasButton(html, "暂停采集")).toBe(true);
    expect(hasButton(html, "提交批次")).toBe(true);
    expect(hasButton(html, "开始采集")).toBe(false);
    expect(hasButton(html, "取消任务")).toBe(false);
  });

  it("空数组不渲染动作按钮", () => {
    const html = renderBar([]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("没有可执行的批次动作");
  });

  it("字段缺失时明确提示，不按状态推断", () => {
    const html = renderBar(undefined);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("服务端未下发allowed_actions");
  });

  it("未知动作被忽略，不猜语义", () => {
    const html = renderBar(["republish"]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("没有可执行的批次动作");
  });
});
