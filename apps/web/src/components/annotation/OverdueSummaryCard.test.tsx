import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type { AnnotationOverdueSummary } from "../../types/annotationOverdue";
import { OverdueSummaryBody } from "./OverdueSummaryCard";
import type { OverdueSummaryBodyProps } from "./OverdueSummaryCard";

/** AntD 会在中文与计数之间插入空白，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function summaryFixture(
  overrides: Partial<AnnotationOverdueSummary> = {},
): AnnotationOverdueSummary {
  return {
    project_id: "11111111-1111-1111-1111-111111111111",
    evaluated_at: "2026-11-19T12:00:00Z",
    total_overdue: 3,
    earliest_due_at: "2026-11-15T12:00:00Z",
    by_priority: [
      { priority: "P0", count: 1 },
      { priority: "P1", count: 1 },
      { priority: "P2", count: 1 },
      { priority: "P3", count: 0 },
    ],
    by_status: [
      { status: "IN_PROGRESS", count: 2 },
      { status: "BLOCKED", count: 1 },
    ],
    buckets: [
      { bucket: "LT_24H", count: 1, lower_bound_hours: 0, upper_bound_hours: 24 },
      { bucket: "D1_TO_D3", count: 1, lower_bound_hours: 24, upper_bound_hours: 72 },
      { bucket: "GT_3D", count: 1, lower_bound_hours: 72, upper_bound_hours: null },
    ],
    ...overrides,
  };
}

function renderBody(
  data: AnnotationOverdueSummary | null,
  overrides: Partial<OverdueSummaryBodyProps> = {},
): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <OverdueSummaryBody
          data={data}
          loading={false}
          stale={false}
          error={null}
          onRetry={() => undefined}
          {...overrides}
        />
      </MemoryRouter>,
    ),
  );
}

describe("标注逾期汇总卡片状态", () => {
  it("Loading：首次加载展示加载状态", () => {
    const html = renderBody(null, { loading: true });
    expect(html).toContain("正在加载逾期汇总");
  });

  it("Empty：没有逾期任务时如实展示 0，不渲染异常", () => {
    const html = renderBody(
      summaryFixture({
        total_overdue: 0,
        earliest_due_at: null,
        by_priority: [
          { priority: "P0", count: 0 },
          { priority: "P1", count: 0 },
          { priority: "P2", count: 0 },
          { priority: "P3", count: 0 },
        ],
        by_status: [],
        buckets: [
          { bucket: "LT_24H", count: 0, lower_bound_hours: 0, upper_bound_hours: 24 },
          { bucket: "D1_TO_D3", count: 0, lower_bound_hours: 24, upper_bound_hours: 72 },
          { bucket: "GT_3D", count: 0, lower_bound_hours: 72, upper_bound_hours: null },
        ],
      }),
    );
    expect(html).toContain("当前项目没有逾期任务");
    expect(html).not.toContain("SLA分桶");
  });

  it("Error：非权限错误展示错误状态与重试", () => {
    const html = renderBody(null, {
      error: new ApiError({ kind: "http", message: "服务异常", status: 500 }),
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("重试");
  });

  it("Forbidden：403 展示无权访问", () => {
    const html = renderBody(null, {
      error: new ApiError({ kind: "http", message: "无权访问", status: 403 }),
    });
    expect(html).toContain("无权访问");
  });

  it("Stale：刷新失败保留旧数据并提示过期", () => {
    const html = renderBody(summaryFixture(), { stale: true });
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("逾期任务数");
  });

  it("就绪：渲染总数、分桶、优先级与状态分布", () => {
    const html = renderBody(summaryFixture());
    expect(html).toContain("逾期任务数");
    expect(html).toContain("24小时以内：1");
    expect(html).toContain("1–3天：1");
    expect(html).toContain("超过3天：1");
    expect(html).toContain("P0：1");
    expect(html).toContain("进行中：2");
    expect(html).toContain("已阻塞：1");
    expect(html).toContain("只读");
  });
});
