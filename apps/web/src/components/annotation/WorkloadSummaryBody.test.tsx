import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type { AnnotationWorkload } from "../../types/annotationWorkload";
import { WorkloadSummaryBody } from "./WorkloadSummaryBody";
import type { WorkloadSummaryBodyProps } from "./WorkloadSummaryBody";

/** AntD 会在中文与数字之间插空白，断言前先去掉。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function workloadFixture(
  overrides: Partial<AnnotationWorkload> = {},
): AnnotationWorkload {
  return {
    project_id: "22222222-2222-2222-2222-222222222222",
    user_id: "11111111-1111-1111-1111-111111111111",
    period_start: "2026-11-01T00:00:00Z",
    period_end: "2026-11-08T00:00:00Z",
    assigned_item_count: 4,
    claimed_item_count: 2,
    submitted_item_count: 1,
    approved_item_count: 1,
    rework_item_count: 0,
    total_minutes: 90,
    capacity_minutes: null,
    utilization_ratio: null,
    status: "NORMAL",
    missing_semantics: "NONE",
    source_version: "annotation-assignment-events/1",
    ...overrides,
  };
}

function renderBody(overrides: Partial<WorkloadSummaryBodyProps> = {}): string {
  return squash(
    renderToStaticMarkup(
      <WorkloadSummaryBody
        data={workloadFixture()}
        loading={false}
        stale={false}
        error={null}
        onRetry={() => {}}
        assigneeLabel="张三"
        {...overrides}
      />,
    ),
  );
}

describe("WorkloadSummaryBody", () => {
  it("容量未配置时显示「未配置」而不是 0", () => {
    const html = renderBody();

    expect(html).toContain("未配置");
    expect(html).toContain("未配置容量，不计算利用率");
    // 不能把 null 渲染成 "0 分钟"。
    expect(html).not.toContain("周期容量</span>0分钟");
  });

  it("容量已配置时显示真实容量与百分比利用率", () => {
    const html = renderBody({
      data: workloadFixture({
        capacity_minutes: 120,
        utilization_ratio: 0.75,
        status: "AT_CAPACITY",
      }),
    });

    expect(html).toContain("2小时");
    expect(html).toContain("75.0%");
    expect(html).toContain("达到容量");
  });

  it("口径不完整时给出显式警告，不冒充完整", () => {
    const html = renderBody({
      data: workloadFixture({ missing_semantics: "PARTIAL" }),
    });

    expect(html).toContain("统计口径不完整");
    expect(html).toContain("部分事件缺失");
  });

  it("口径完整时不渲染警告", () => {
    const html = renderBody();

    expect(html).not.toContain("统计口径不完整");
  });

  it("Stale 时保留数据并说明刷新失败", () => {
    const html = renderBody({ stale: true });

    expect(html).toContain("最近一次刷新失败");
    expect(html).toContain("张三");
  });

  it("加载中不渲染旧数据", () => {
    const html = renderBody({ data: null, loading: true });

    expect(html).toContain("正在加载工作量");
    expect(html).not.toContain("分配项数");
  });

  it("首次失败显示错误态与重试入口", () => {
    const html = renderBody({
      data: null,
      loading: false,
      error: new ApiError({ kind: "network", message: "网络不可用" }),
    });

    expect(html).toContain("网络不可用");
    expect(html).toContain("重试");
  });
});
