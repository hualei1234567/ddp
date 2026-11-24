import { describe, expect, it } from "vitest";

import { parseAssignmentCapacityPreview } from "./annotationCapacityPreviewParsers";
import { ContractValidationError } from "./validation";

const ASSIGNEE_ID = "11111111-1111-1111-1111-111111111111";

function payload(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    assignee_id: ASSIGNEE_ID,
    open_item_count: 2,
    open_planned_minutes: 60,
    planned_item_count: 3,
    planned_minutes: 90,
    projected_item_count: 5,
    projected_planned_minutes: 150,
    max_open_items: 10,
    max_planned_minutes: 300,
    item_capacity_ratio: 0.5,
    minutes_capacity_ratio: 0.5,
    will_exceed: false,
    exceeds_open_items: false,
    exceeds_planned_minutes: false,
    status: "NORMAL",
    ...overrides,
  };
}

describe("parseAssignmentCapacityPreview", () => {
  it("解析正常负载的响应", () => {
    const preview = parseAssignmentCapacityPreview(payload());

    expect(preview.assignee_id).toBe(ASSIGNEE_ID);
    expect(preview.open_item_count).toBe(2);
    expect(preview.projected_item_count).toBe(5);
    expect(preview.will_exceed).toBe(false);
    expect(preview.status).toBe("NORMAL");
  });

  it("上限未配置时 max 与比率保持 null，不降级成 0", () => {
    const preview = parseAssignmentCapacityPreview(
      payload({
        max_open_items: null,
        max_planned_minutes: null,
        item_capacity_ratio: null,
        minutes_capacity_ratio: null,
      }),
    );

    expect(preview.max_open_items).toBeNull();
    expect(preview.max_planned_minutes).toBeNull();
    expect(preview.item_capacity_ratio).toBeNull();
    expect(preview.minutes_capacity_ratio).toBeNull();
  });

  it("上限与比率只有一个有值时报契约错误", () => {
    expect(() =>
      parseAssignmentCapacityPreview(payload({ max_open_items: null })),
    ).toThrow(ContractValidationError);
    expect(() =>
      parseAssignmentCapacityPreview(
        payload({ minutes_capacity_ratio: null }),
      ),
    ).toThrow(ContractValidationError);
  });

  it("will_exceed 与两个分项布尔不自洽时报契约错误", () => {
    // 整体说不超，但项数说超 —— UI 会给出矛盾提示。
    expect(() =>
      parseAssignmentCapacityPreview(
        payload({ will_exceed: false, exceeds_open_items: true }),
      ),
    ).toThrow(ContractValidationError);
    // 整体说超，但两个分项都说不超 —— 警示没有依据。
    expect(() =>
      parseAssignmentCapacityPreview(payload({ will_exceed: true })),
    ).toThrow(ContractValidationError);
  });

  it("布尔字段不用 truthy 兜底", () => {
    expect(() =>
      parseAssignmentCapacityPreview(payload({ will_exceed: "false" })),
    ).toThrow(ContractValidationError);
  });

  it("未知 status 不被兜底成 NORMAL", () => {
    expect(() =>
      parseAssignmentCapacityPreview(payload({ status: "SOMETHING_NEW" })),
    ).toThrow(ContractValidationError);
  });

  it("缺字段直接报错，不静默补 0", () => {
    const incomplete = payload();
    delete incomplete.open_item_count;
    expect(() => parseAssignmentCapacityPreview(incomplete)).toThrow(
      ContractValidationError,
    );
  });
});
