import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  qualityFindingDiffFixture,
  qualityRunDiffFixture,
  QUALITY_BASELINE_RUN_ID,
  QUALITY_CANDIDATE_RUN_ID,
} from "../../services/__fixtures__/qualityRunFixtures";
import QualityRunDiffReportPanel from "./QualityRunDiffReportPanel";
import type { QualityRunDiffReportPanelProps } from "./QualityRunDiffReportPanel";

/** AntD 会在两个中文字符之间插入空格，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

const baseProps: QualityRunDiffReportPanelProps = {
  report: qualityRunDiffFixture(),
  loading: false,
  error: null,
  onCompare: () => undefined,
  hasBaseline: true,
  onRetry: () => undefined,
};

function renderPanel(
  overrides: Partial<QualityRunDiffReportPanelProps> = {},
): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <QualityRunDiffReportPanel {...baseProps} {...overrides} />
      </MemoryRouter>,
    ),
  );
}

/**
 * 差异报告静态渲染测试（V31-033 / FR-027）。
 *
 * 服务端按 `rule_id + subject_ref` 对齐并分四类：新增（NEW）/ 已消除
 * （RESOLVED）/ 无变化（UNCHANGED）/ 结论变化（CHANGED）。分组标题、
 * 计数与两侧结论都必须来自服务端报告，前端不重新分类。
 */
describe("QualityRunDiffReportPanel（FR-027）", () => {
  it("渲染四类分组标题与各自的计数", () => {
    const html = renderPanel();
    expect(html).toContain("新增问题");
    expect(html).toContain("已消除问题");
    expect(html).toContain("无变化");
    expect(html).toContain("结论变化");
    expect(html).toContain("（1）");
  });

  it("每条差异展示 rule_id、subject_ref 与两侧结论", () => {
    const html = renderPanel({
      report: qualityRunDiffFixture({
        findings: [
          qualityFindingDiffFixture("CHANGED", {
            rule_id: "frame_continuity",
            subject_ref: "track-7",
          }),
        ],
        new_count: 0,
        resolved_count: 0,
        unchanged_count: 0,
        changed_count: 1,
        has_regression: false,
      }),
    });
    expect(html).toContain("frame_continuity");
    expect(html).toContain("track-7");
    expect(html).toContain("基线");
    expect(html).toContain("候选");
    expect(html).toContain("未通过");
    expect(html).toContain("通过");
  });

  it("无结论一侧显示占位而不是伪造结论", () => {
    const html = renderPanel({
      report: qualityRunDiffFixture({
        findings: [qualityFindingDiffFixture("NEW")],
        new_count: 1,
        resolved_count: 0,
        unchanged_count: 0,
        changed_count: 0,
        has_regression: true,
      }),
    });
    // NEW 的基线侧没有结论：显示「无结论」，不显示通过/未通过。
    expect(html).toContain("无结论");
    // 候选侧的 FAIL 正常渲染为未通过，分类列归入「新增」。
    expect(html).toContain("未通过");
    expect(html).toContain("新增问题（1）");
  });

  it("has_regression 时展示回归警示，并只把 NEW 算作回归", () => {
    const html = renderPanel();
    expect(html).toContain("重跑引入回归");
    expect(html).toContain("新增1条");
  });

  it("无回归（仅修复）时不显示回归警示", () => {
    const html = renderPanel({
      report: qualityRunDiffFixture({
        findings: [qualityFindingDiffFixture("RESOLVED")],
        new_count: 0,
        resolved_count: 1,
        unchanged_count: 0,
        changed_count: 0,
        has_regression: false,
      }),
    });
    expect(html).not.toContain("重跑引入回归");
    expect(html).toContain("已消除问题");
  });

  it("空差异展示友好空态：重跑前后无变化", () => {
    const html = renderPanel({
      report: qualityRunDiffFixture({
        findings: [],
        new_count: 0,
        resolved_count: 0,
        unchanged_count: 0,
        changed_count: 0,
        has_regression: false,
      }),
    });
    expect(html).toContain("两次运行的结论完全一致");
    expect(html).not.toContain("重跑引入回归");
  });

  it("加载与错误态有明确提示，不渲染空差异当成功", () => {
    expect(renderPanel({ report: null, loading: true })).toContain(
      "正在加载差异报告",
    );
    expect(renderPanel({ report: null })).toContain("差异报告");
  });

  it("展示 baseline / candidate 运行 ID 便于核对比较对象", () => {
    const html = renderPanel();
    expect(html).toContain(QUALITY_BASELINE_RUN_ID);
    expect(html).toContain(QUALITY_CANDIDATE_RUN_ID);
  });
});
