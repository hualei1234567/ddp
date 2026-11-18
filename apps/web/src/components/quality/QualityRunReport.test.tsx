import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  QUALITY_IMPORT_ID,
  qualityRunFindingFixture,
  qualityRunFixture,
  qualityRunWithCategoryStatus,
} from "../../services/__fixtures__/qualityRunFixtures";
import { ApiError } from "../../services/api";
import QualityRunReport from "./QualityRunReport";
import type { QualityRunReportProps } from "./QualityRunReport";

/** AntD 会在两个中文字符之间插入空格，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

const assetsHref = `/data-source-imports/${QUALITY_IMPORT_ID}/assets`;

const baseProps: QualityRunReportProps = {
  phase: "ready",
  run: qualityRunFixture(),
  refreshing: false,
  stale: false,
  error: null,
  running: false,
  runError: null,
  findings: {
    items: [qualityRunFindingFixture()],
    loading: false,
    refreshing: false,
    loadingMore: false,
    hasMore: false,
    error: null,
    loadMoreError: null,
    stale: false,
  },
  category: null,
  severity: null,
  onCategoryChange: () => undefined,
  onSeverityChange: () => undefined,
  onReload: () => undefined,
  onRun: () => undefined,
  onLoadMoreFindings: () => undefined,
  assetsHref,
  disabledReason: null,
};

function renderReport(overrides: Partial<QualityRunReportProps> = {}): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <QualityRunReport {...baseProps} {...overrides} />
      </MemoryRouter>,
    ),
  );
}

describe("统一质量报告：空态与六类摘要", () => {
  it("没有运行 ID 时显示「尚未质检」，并说明触发只追加", () => {
    const html = renderReport({ run: null, findings: { ...baseProps.findings, items: [] } });

    expect(html).toContain("尚未质检");
    expect(html).toContain("执行质量检查");
    expect(html).toContain("只追加");
    expect(html).not.toContain("FAILED（存在阻断违规）");
  });

  it("六类摘要全部渲染，包含派生 OVERALL", () => {
    const html = renderReport();

    expect(html).toContain("完整性（INTEGRITY）");
    expect(html).toContain("时序（TEMPORAL）");
    expect(html).toContain("视觉（VISUAL）");
    expect(html).toContain("元数据（METADATA）");
    expect(html).toContain("标注（ANNOTATION）");
    expect(html).toContain("总体（OVERALL）");
  });

  it("INCOMPLETE 明确写「无结论」，绝不渲染成 PASSED", () => {
    const html = renderReport({
      run: qualityRunFixture({ status: "INCOMPLETE", score: null }),
    });

    expect(html).toContain("INCOMPLETE（无结论）");
    expect(html).toContain("不得当作PASSED使用");
    expect(html).not.toContain("PASSED（无阻断违规）");
  });

  it("NOT_APPLICABLE 类别显示为不适用/无结论，并给出汇总提示", () => {
    const html = renderReport({
      run: qualityRunWithCategoryStatus("ANNOTATION", "NOT_APPLICABLE", null),
    });

    expect(html).toContain("NOT_APPLICABLE（不适用，无结论）");
    expect(html).toContain("1个类别没有结论（不适用）");
    expect(html).toContain("不代表通过");
  });

  it("FAILED 显示阻断文案；PASSED 只声明无阻断", () => {
    expect(
      renderReport({
        run: qualityRunFixture({ status: "FAILED", error_count: 1 }),
      }),
    ).toContain("FAILED（存在阻断违规）");
    expect(renderReport()).toContain("PASSED（无阻断违规）");
    expect(renderReport()).toContain("PASSED只表示没有ERROR级阻断违规");
  });
});

describe("统一质量报告：结论筛选与下钻", () => {
  it("渲染类别/严重度筛选器与已加载范围说明", () => {
    const html = renderReport({
      severity: "ERROR",
      findings: { ...baseProps.findings, hasMore: true },
    });

    expect(html).toContain("按类别筛选质量结论");
    expect(html).toContain("按严重度筛选质量结论");
    expect(html).toContain("已加载1条结论");
    expect(html).toContain("严重度筛选只覆盖已加载结论");
  });

  it("NOT_APPLICABLE 与 ERROR 结论都不显示为通过", () => {
    const html = renderReport({
      findings: {
        ...baseProps.findings,
        items: [
          qualityRunFindingFixture({
            finding_id: "77777777-7777-4777-8777-777777777777",
            status: "NOT_APPLICABLE",
            score: null,
          }),
          qualityRunFindingFixture({
            finding_id: "88888888-8888-4888-8888-888888888888",
            status: "ERROR",
            score: null,
          }),
        ],
      },
    });

    expect(html).toContain("无结论");
    expect(html).toContain("执行失败（无结论）");
    expect(html).toContain("无可用评分（无结论）");
  });

  it("结论行提供到既有媒体资产页的下钻链接", () => {
    const html = renderReport();

    expect(html).toContain(assetsHref);
    expect(html).toContain("查看媒体资产");
  });

  it("运行不是导入级时展示 Disabled 状态并说明不可下钻", () => {
    const html = renderReport({
      disabledReason: "该运行不是导入级检查，无法在本页下钻导入媒体资产。",
    });

    expect(html).toContain("本页无法下钻该运行");
    expect(html).toContain("无法在本页下钻导入媒体资产");
  });
});

describe("统一质量报告：请求状态", () => {
  it("Loading / Error / Forbidden 状态如实展示", () => {
    expect(renderReport({ phase: "loading", run: null })).toContain(
      "正在加载统一质量检查",
    );

    const error = new ApiError({ kind: "network", message: "网络超时" });
    expect(renderReport({ phase: "error", run: null, error })).toContain(
      "网络超时",
    );

    const forbidden = new ApiError({
      kind: "http",
      status: 403,
      message: "无权限",
    });
    expect(
      renderReport({ phase: "forbidden", run: null, error: forbidden }),
    ).toContain("无权访问");
  });

  it("运行 ID 无法读取时提供重试与新建运行入口，不停留在死链", () => {
    const notFound = new ApiError({
      kind: "http",
      status: 404,
      message: "质量运行不存在",
    });
    const html = renderReport({ phase: "not-found", run: null, error: notFound });

    expect(html).toContain("质量运行不存在");
    expect(html).toContain("执行一次新的质量检查");
  });

  it("刷新失败但保留旧数据时展示 Stale，而不是清空结论", () => {
    const html = renderReport({ stale: true });

    expect(html).toContain("刷新失败，展示上次成功获取的质量运行");
    expect(html).toContain("完整性（INTEGRITY）");
  });

  it("触发运行失败时展示错误并可重试", () => {
    const runError = new ApiError({
      kind: "http",
      status: 500,
      message: "执行质量检查失败",
    });
    const html = renderReport({ runError });

    expect(html).toContain("执行质量检查失败");
    expect(html).toContain("重试");
  });

  it("结论明细加载失败时展示错误，不冒充空列表", () => {
    const findingsError = new ApiError({
      kind: "network",
      message: "结论明细加载失败",
    });
    const html = renderReport({
      findings: {
        ...baseProps.findings,
        items: [],
        error: findingsError,
      },
    });

    expect(html).toContain("结论明细加载失败");
  });

  it("结论明细部分失败时保留已加载条目并标记 Partial", () => {
    const loadMoreError = new ApiError({
      kind: "network",
      message: "加载更多超时",
    });
    const html = renderReport({
      findings: {
        ...baseProps.findings,
        hasMore: true,
        loadMoreError,
      },
    });

    expect(html).toContain("加载更多结论失败：加载更多超时");
    expect(html).toContain("已展示的结论仍然有效");
  });
});
