import { describe, expect, it } from "vitest";

import { ContractValidationError } from "./validation";
import { parseAnnotationWorkload } from "./annotationWorkloadParsers";

const PROJECT_ID = "22222222-2222-2222-2222-222222222222";
const USER_ID = "11111111-1111-1111-1111-111111111111";

function payload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    user_id: USER_ID,
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
    missing_semantics: "N/A",
    source_version: "annotation-assignment-events/1",
    ...overrides,
  };
}

describe("parseAnnotationWorkload", () => {
  it("解析容量未配置的响应：null 保持为 null", () => {
    const workload = parseAnnotationWorkload(payload());

    expect(workload.assigned_item_count).toBe(4);
    expect(workload.total_minutes).toBe(90);
    expect(workload.capacity_minutes).toBeNull();
    expect(workload.utilization_ratio).toBeNull();
    expect(workload.status).toBe("NORMAL");
    expect(workload.missing_semantics).toBe("N/A");
    expect(workload.source_version).toBe("annotation-assignment-events/1");
  });

  it("解析已配置容量的响应", () => {
    const workload = parseAnnotationWorkload(
      payload({
        capacity_minutes: 120,
        utilization_ratio: 0.75,
        status: "AT_CAPACITY",
        missing_semantics: "NONE",
      }),
    );

    expect(workload.capacity_minutes).toBe(120);
    expect(workload.utilization_ratio).toBe(0.75);
    expect(workload.status).toBe("AT_CAPACITY");
  });

  it("容量与利用率只出现一个时报契约错误", () => {
    expect(() =>
      parseAnnotationWorkload(payload({ capacity_minutes: 120 })),
    ).toThrow(ContractValidationError);
    expect(() =>
      parseAnnotationWorkload(payload({ utilization_ratio: 0.5 })),
    ).toThrow(/同时为空或同时有值/);
  });

  it("未知状态不兜底成 NORMAL", () => {
    expect(() =>
      parseAnnotationWorkload(payload({ status: "TOTALLY_FINE" })),
    ).toThrow(/工作量汇总.status/);
  });

  it("缺失字段直接失败，不静默补 0", () => {
    const broken = payload();
    delete broken.total_minutes;
    expect(() => parseAnnotationWorkload(broken)).toThrow(/total_minutes/);
  });

  it("负数计数被拒绝", () => {
    expect(() =>
      parseAnnotationWorkload(payload({ assigned_item_count: -1 })),
    ).toThrow(/assigned_item_count/);
  });
});
