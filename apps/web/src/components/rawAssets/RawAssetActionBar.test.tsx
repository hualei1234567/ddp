import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RawAssetActionBar from "./RawAssetActionBar";

function render(actions: string[]): string {
  return renderToStaticMarkup(
    <RawAssetActionBar actions={actions} onAction={async () => undefined} />,
  );
}

describe("原始资产动作条", () => {
  it("没有可执行动作时明确说明，而不是空白", () => {
    expect(render([])).toContain("当前状态没有可执行动作");
  });

  it("隔离资产只渲染放行与驳回，不渲染归档", () => {
    const html = render([
      "release-quarantine-or-revalidate",
      "reject-quarantined",
      "tombstone",
    ]);
    expect(html).toContain("隔离放行并重新校验");
    expect(html).toContain("驳回隔离资产");
    expect(html).toContain("墓碑化");
    expect(html).not.toContain(">归档<");
  });

  it("未知动作被忽略，不会渲染出按了必然 422 的按钮", () => {
    const html = render([" archive ", "system-fail"]);
    expect(html).toContain("当前状态没有可执行动作");
    expect(html).not.toContain("system-fail");
  });
});
