import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import RawAssetListPage from "./RawAssetListPage";

function render(path: string): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <RawAssetListPage />
    </MemoryRouter>,
  );
}

describe("数据资产列表页", () => {
  it("没有项目上下文时给出统一空态，不渲染资产表格", () => {
    const html = render("/assets");
    expect(html).toContain("未选择项目");
    expect(html).not.toContain("已加载");
  });

  it("带 project_id 进入时进入加载态（不静默空白）", () => {
    // 页面挂载即发起请求：此处只断言首屏有可见状态，不发真实网络请求
    // （node 环境下 useCursorPage 的首帧就是 loading）。
    const html = render(
      "/assets?project_id=11111111-1111-1111-1111-111111111111",
    );
    expect(html).toContain("数据资产");
    expect(html).toMatch(/正在加载|没有数据资产|无权/);
  });
});
