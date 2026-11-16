import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { QUALITY_PROJECT_ID } from "../../services/__fixtures__/qualityFixtures";
import QualityOverviewPage from "./QualityOverviewPage";

function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function renderPage(search: string): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter initialEntries={[`/${search}`]}>
        <QualityOverviewPage />
      </MemoryRouter>,
    ),
  );
}

describe("质量管理总入口", () => {
  it("四类质量能力都能从总入口到达", () => {
    const html = renderPage(`?project_id=${QUALITY_PROJECT_ID}`);

    expect(html).toContain("质量管理");
    expect(html).toContain("金标准集（黄金集）");
    expect(html).toContain("质检抽检");
    expect(html).toContain("IAA报告");
    expect(html).toContain("标注质量检查");
    // 链接带上项目上下文，避免落到「未选择项目」空态。
    expect(html).toContain(
      `/quality/gold-sets?project_id=${QUALITY_PROJECT_ID}`,
    );
    expect(html).toContain(
      `/quality/quality-checks?project_id=${QUALITY_PROJECT_ID}`,
    );
  });

  it("口径说明明确「无结论」不等于通过", () => {
    const html = renderPage(`?project_id=${QUALITY_PROJECT_ID}`);

    expect(html).toContain("不得理解为通过");
    expect(html).toContain("有任何ERROR即FAILED");
  });

  it("没有项目上下文时不渲染入口，提示按项目隔离", () => {
    const html = renderPage("");

    expect(html).toContain("未选择项目");
    expect(html).not.toContain("进入金标准集");
  });
});
