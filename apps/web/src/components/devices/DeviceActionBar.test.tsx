import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import DeviceActionBar from "./DeviceActionBar";

function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

function renderBar(
  allowedActions: string[] | undefined,
  extra: { disabledReason?: string | null } = {},
): string {
  return renderToStaticMarkup(
    <DeviceActionBar
      allowedActions={allowedActions}
      entityLabel="设备"
      onAction={() => Promise.resolve()}
      {...extra}
    />,
  );
}

describe("设备动作条", () => {
  it("有 allowed_actions 时只渲染其中已知动作", () => {
    const html = renderBar(["activate", "suspend"]);

    expect(hasButton(html, "启用")).toBe(true);
    expect(hasButton(html, "挂起")).toBe(true);
    expect(hasButton(html, "退役")).toBe(false);
    expect(hasButton(html, "进入维修")).toBe(false);
  });

  it("空数组不渲染按钮", () => {
    const html = renderBar([]);

    expect(html).not.toContain("<button");
    expect(squash(html)).toContain("没有可执行的动作");
  });

  it("字段缺失时按契约动作集渲染并显式提示", () => {
    const html = renderBar(undefined);

    expect(squash(html)).toContain("服务端未下发allowed_actions");
    expect(hasButton(html, "启用")).toBe(true);
    expect(hasButton(html, "进入维修")).toBe(true);
    expect(hasButton(html, "上报故障")).toBe(true);
    expect(hasButton(html, "挂起")).toBe(true);
    expect(hasButton(html, "退役")).toBe(true);
  });

  it("整体禁用原因会禁用按钮", () => {
    const html = renderBar(["activate"], { disabledReason: "离线时不允许操作" });

    expect(html).toContain("disabled");
  });
});
