import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  qualityFindingFixture,
} from "../../services/__fixtures__/qualityFixtures";
import { ApiError } from "../../services/api";
import QualityFindingTable from "./QualityFindingTable";

/** AntD 会在两个中文字符的文本间插入空格，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function renderTable(
  findings: ReturnType<typeof qualityFindingFixture>[],
  overrides: Partial<Parameters<typeof QualityFindingTable>[0]> = {},
): string {
  return squash(
    renderToStaticMarkup(
      <QualityFindingTable findings={findings} {...overrides} />,
    ),
  );
}

describe("质量检查结果表的三态呈现", () => {
  it("passed=null 渲染为「无结论」，绝不渲染成「通过」", () => {
    const html = renderTable([qualityFindingFixture({ passed: null })]);

    expect(html).toContain("无结论");
    // 关键断言：表格正文与计数区都不能出现「通过」字样。
    expect(html).not.toContain(">通过<");
    expect(html).not.toContain("通过1");
    expect(html).toContain("未通过0");
  });

  it("passed=true 渲染「通过」", () => {
    const html = renderTable([qualityFindingFixture({ passed: true })]);

    expect(html).toContain(">通过<");
    expect(html).toContain("通过1");
    expect(html).toContain("无结论0");
  });

  it("passed=false 渲染「未通过」并计入未通过", () => {
    const html = renderTable([qualityFindingFixture({ passed: false })]);

    expect(html).toContain("未通过");
    expect(html).toContain("未通过1");
    expect(html).toContain("通过0");
  });

  it("混合三态时计数分开，无结论不并入通过", () => {
    const html = renderTable([
      qualityFindingFixture({ passed: true }),
      qualityFindingFixture({ passed: false, rule_id: "boundary" }),
      qualityFindingFixture({ passed: null, rule_id: "threshold" }),
    ]);

    expect(html).toContain("通过1");
    expect(html).toContain("未通过1");
    expect(html).toContain("无结论1");
    expect(html).toContain("规则结果共3条");
  });

  it("存在无结论时给出「不代表合规」的说明", () => {
    const html = renderTable([qualityFindingFixture({ passed: null })]);

    expect(html).toContain("条规则没有结论");
    expect(html).toContain("不代表合规");
  });

  it("ERROR / WARN 级别都标出语义", () => {
    const html = renderTable([
      qualityFindingFixture({ severity: "ERROR" }),
      qualityFindingFixture({ severity: "WARN", rule_id: "confidence" }),
    ]);

    expect(html).toContain("ERROR（阻断）");
    expect(html).toContain("WARN（提醒）");
  });
});

describe("质量检查结果表的空态与错误态", () => {
  it("空结果明确说明「不代表通过」", () => {
    const html = renderTable([]);

    expect(html).toContain("本次检查没有任何规则结果（不代表通过）");
    expect(html).toContain("规则结果共0条");
  });

  it("首次加载失败展示错误态与重试", () => {
    const error = new ApiError({
      kind: "http",
      status: 500,
      message: "服务异常",
      code: "internal_error",
    });
    const html = renderTable([], { error, onRetry: () => undefined });

    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    expect(html).toContain("重试");
  });

  it("403 展示为无权限，而不是通用错误", () => {
    const error = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问",
      code: "forbidden",
    });
    const html = renderTable([], { error });

    expect(html).toContain("无权访问");
    expect(html).toContain("forbidden");
    // 403 不能退化成通用「加载失败」，否则用户会去重试而不是找权限。
    expect(html).not.toContain("加载失败");
  });

  it("刷新失败但已有数据时标记为过期而不是清空", () => {
    const error = new ApiError({
      kind: "network",
      message: "网络中断",
    });
    const html = renderTable([qualityFindingFixture()], { error });

    expect(html).toContain("规则结果共1条");
    expect(html).toContain("最近一次刷新失败");
    expect(html).toContain("网络中断");
  });
});
