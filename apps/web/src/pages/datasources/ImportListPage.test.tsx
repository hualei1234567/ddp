import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ImportListPage from "./ImportListPage";

const PROJECT = "11111111-1111-1111-1111-111111111111";

function render(path: string): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <ImportListPage />
    </MemoryRouter>,
  );
}

describe("导入任务列表页", () => {
  it("没有项目上下文时给出统一空态，不渲染筛选与表格", () => {
    const html = render("/data-source-imports");

    expect(html).toContain("未选择项目");
    expect(html).not.toContain("自动刷新");
  });

  it("带 project_id 进入时进入加载态并保留手动刷新入口", () => {
    // 页面挂载即发起请求：这里只断言首屏有可见状态，不发真实网络请求
    // （node 环境下 useCursorPage 的首帧就是 loading）。
    const html = render(`/data-source-imports?project_id=${PROJECT}`);

    expect(html).toContain("导入任务");
    expect(html).toContain("刷新");
    expect(html).toMatch(/正在加载|暂无导入任务|加载失败|无权/);
  });

  it("尚无运行中任务时不提示自动刷新（避免误导为空转轮询）", () => {
    const html = render(`/data-source-imports?project_id=${PROJECT}`);

    expect(html).not.toContain("自动刷新");
  });

  it("状态筛选由 URL 参数驱动，刷新页面后筛选条件不丢失", () => {
    const html = render(
      `/data-source-imports?project_id=${PROJECT}&status=RUNNING`,
    );

    // 静态首帧只校验筛选控件已按 URL 回填，不校验服务端返回内容。
    expect(html).toContain("RUNNING");
  });
});
