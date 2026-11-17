import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  QUALITY_SUBMISSION_ID,
  qualityCheckRunFixture,
  qualityFindingFixture,
} from "../../services/__fixtures__/qualityFixtures";
import { ApiError } from "../../services/api";
import QualityCheckBody from "./QualityCheckBody";
import type { QualityCheckBodyProps } from "./QualityCheckBody";

/** AntD 在两个中文字符的按钮文本间插入空格（"重 试"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

const baseProps: QualityCheckBodyProps = {
  submissionId: QUALITY_SUBMISSION_ID,
  phase: "ready",
  runs: [qualityCheckRunFixture()],
  selectedRunId: null,
  onSelectRun: () => undefined,
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  stale: false,
  error: null,
  loadMoreError: null,
  rerunning: false,
  rerunError: null,
  onReload: () => undefined,
  onLoadMore: () => undefined,
  onRerun: () => undefined,
};

function renderBody(overrides: Partial<QualityCheckBodyProps> = {}): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <QualityCheckBody {...baseProps} {...overrides} />
      </MemoryRouter>,
    ),
  );
}

describe("提交质量检查页的列表与三态呈现", () => {
  it("渲染检查历史与逐条结论", () => {
    const html = renderBody();

    expect(html).toContain("标注质量检查");
    expect(html).toContain("无ERROR级违规");
    expect(html).toContain("规则结果共1条");
    expect(html).toContain("通过1");
    expect(html).toContain("无结论0");
  });

  it("全部无结论时明确提示 PASSED 不代表质量合格", () => {
    const html = renderBody({
      runs: [
        qualityCheckRunFixture({
          findings: [
            qualityFindingFixture({ passed: null, rule_id: "boundary" }),
            qualityFindingFixture({ passed: null, rule_id: "threshold" }),
          ],
        }),
      ],
    });

    expect(html).toContain("本次检查没有任何规则给出结论");
    expect(html).toContain("不代表标注质量合格");
    expect(html).toContain("无结论2");
  });

  it("无结论不会被算作通过：只有 null 时不出现「通过1」", () => {
    const html = renderBody({
      runs: [
        qualityCheckRunFixture({
          findings: [qualityFindingFixture({ passed: null })],
        }),
      ],
    });

    expect(html).toContain("通过0");
    expect(html).toContain("无结论1");
  });

  it("FAILED 记录展示 ERROR 级违规文案与计数", () => {
    const html = renderBody({
      runs: [
        qualityCheckRunFixture({
          status: "FAILED",
          error_count: 1,
          findings: [qualityFindingFixture({ passed: false })],
        }),
      ],
    });

    expect(html).toContain("存在ERROR级违规");
    expect(html).toContain("status=FAILED");
    expect(html).toContain("未通过1");
  });

  it("选中某次历史记录时展示该次详情", () => {
    const first = qualityCheckRunFixture({
      run_id: "11111111-1111-4111-8111-111111111111",
      findings: [qualityFindingFixture({ passed: false, rule_id: "first" })],
    });
    const second = qualityCheckRunFixture({
      run_id: "22222222-2222-4222-8222-222222222222",
      findings: [qualityFindingFixture({ passed: true, rule_id: "second" })],
    });

    const html = renderBody({
      runs: [first, second],
      selectedRunId: second.run_id,
    });

    expect(html).toContain("second");
    expect(html).toContain("通过1");
  });
});

describe("提交质量检查页的状态覆盖", () => {
  it("Empty：空历史明确说明「不代表合格」并给出重跑入口", () => {
    const html = renderBody({ runs: [] });

    expect(html).toContain("该提交没有质量检查记录");
    expect(html).toContain("空记录不代表合格");
    expect(hasButton(html, "重新执行检查")).toBe(true);
  });

  it("Loading：首次加载展示加载态", () => {
    const html = renderBody({ phase: "loading", runs: [] });

    expect(html).toContain("正在加载质量检查");
  });

  it("Error：首次加载失败展示错误码与重试", () => {
    const error = new ApiError({
      kind: "http",
      status: 500,
      message: "服务异常",
      code: "internal_error",
    });
    const html = renderBody({ phase: "error", runs: [], error });

    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    expect(hasButton(html, "重试")).toBe(true);
  });

  it("403：展示无权限而不是通用错误", () => {
    const error = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问",
      code: "forbidden",
    });
    const html = renderBody({ phase: "forbidden", runs: [], error });

    expect(html).toContain("无权访问");
    expect(html).not.toContain("加载失败");
  });

  it("Stale：刷新失败保留旧记录并标记过期", () => {
    const error = new ApiError({ kind: "network", message: "网络中断" });
    const html = renderBody({ stale: true, error });

    expect(html).toContain("刷新失败，展示上次成功获取的检查历史");
    expect(html).toContain("规则结果共1条");
  });

  it("Partial：加载更早记录失败时保留已展示的记录", () => {
    const loadMoreError = new ApiError({
      kind: "network",
      message: "网络中断",
    });
    const html = renderBody({ hasMore: true, loadMoreError });

    expect(html).toContain("加载更多失败");
    expect(html).toContain("已展示的检查记录仍然有效");
  });

  it("重跑失败时展示错误，但不隐藏历史记录", () => {
    const rerunError = new ApiError({
      kind: "http",
      status: 422,
      message: "草稿提交不参与质量检查",
      code: "submission_not_submitted",
    });
    const html = renderBody({ rerunError });

    expect(html).toContain("草稿提交不参与质量检查");
    expect(html).toContain("规则结果共1条");
  });
});
