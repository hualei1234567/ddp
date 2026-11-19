import { describe, expect, it } from "vitest";

import { PROJECT_ID } from "./__fixtures__/annotationFixtures";
import { parseAnnotationOverdueSummary } from "./annotationOverdueParsers";
import { ContractValidationError } from "./validation";

function summaryFixture(): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
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
  };
}

describe("逾期汇总严格解析", () => {
  it("解析分布、分桶与最早期逾期时间", () => {
    const summary = parseAnnotationOverdueSummary(summaryFixture());

    expect(summary.project_id).toBe(PROJECT_ID);
    expect(summary.total_overdue).toBe(3);
    expect(summary.earliest_due_at).toBe("2026-11-15T12:00:00Z");
    expect(summary.by_priority.map((item) => item.priority)).toEqual([
      "P0",
      "P1",
      "P2",
      "P3",
    ]);
    expect(summary.buckets.map((item) => item.bucket)).toEqual([
      "LT_24H",
      "D1_TO_D3",
      "GT_3D",
    ]);
    expect(summary.buckets[2]?.upper_bound_hours).toBeNull();
  });

  it("空项目：计数为 0、最早期逾期为 null", () => {
    const summary = parseAnnotationOverdueSummary({
      ...summaryFixture(),
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
    });

    expect(summary.total_overdue).toBe(0);
    expect(summary.earliest_due_at).toBeNull();
    expect(summary.by_status).toEqual([]);
  });

  it("未知分桶拒绝进入 UI", () => {
    const payload = summaryFixture();
    payload.buckets = [
      { bucket: "SOMETHING_ELSE", count: 3, lower_bound_hours: 0, upper_bound_hours: null },
    ];
    expect(() => parseAnnotationOverdueSummary(payload)).toThrow(
      ContractValidationError,
    );
  });

  it("分布之和不等于总数时拒绝（不把契约破坏当成 UI 误差）", () => {
    const payload = summaryFixture();
    payload.total_overdue = 5;
    expect(() => parseAnnotationOverdueSummary(payload)).toThrow(
      /total_overdue/,
    );
  });

  it("缺少分桶边界字段时拒绝", () => {
    const payload = summaryFixture();
    payload.buckets = [{ bucket: "LT_24H", count: 3 }];
    expect(() => parseAnnotationOverdueSummary(payload)).toThrow(
      ContractValidationError,
    );
  });
});
