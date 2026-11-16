import { describe, expect, it } from "vitest";

import {
  actionSpecFixture,
  planFixture,
  scenarioFixture,
  sopFixture,
} from "./__fixtures__/collectionAssetFixtures";
import {
  parseActionSpec,
  parseActionSpecPage,
  parseCollectionPlan,
  parseCollectionSop,
  parseScenario,
} from "./collectionAssetParsers";

describe("采集素材响应解析", () => {
  it("解析采集方案并保留 allowed_actions", () => {
    const plan = parseCollectionPlan(
      planFixture({ status: "ACTIVE", allowed_actions: ["archive"] }),
    );

    expect(plan.plan_version).toBe(2);
    expect(plan.status).toBe("ACTIVE");
    expect(plan.allowed_actions).toEqual(["archive"]);
  });

  it("SOP 的 steps 保持对象数组原样，不解释内部结构", () => {
    const sop = parseCollectionSop(sopFixture());
    expect(sop.steps).toEqual([{ order: 1, instruction: "检查夹爪" }]);
  });

  it("场景标签与动作库条目都能解析", () => {
    expect(parseScenario(scenarioFixture()).scene_labels).toHaveLength(3);
    expect(parseActionSpec(actionSpecFixture()).actions).toEqual([
      { name: "grasp", joints: [0, 1, 2] },
    ]);
  });

  it("未识别状态视为契约变更并拒绝", () => {
    expect(() =>
      parseCollectionPlan({
        ...planFixture(),
        status: "PUBLISHED",
      }),
    ).toThrowError(/枚举/);
  });

  it("allowed_actions 缺失时报错，不默认成空数组", () => {
    const broken: Record<string, unknown> = { ...planFixture() };
    delete broken.allowed_actions;

    expect(() => parseCollectionPlan(broken)).toThrowError(/数组/);
  });

  it("分页结构校验 items 与 next_cursor", () => {
    const page = parseActionSpecPage({
      items: [actionSpecFixture()],
      next_cursor: "cursor-1",
    });

    expect(page.items).toHaveLength(1);
    expect(page.next_cursor).toBe("cursor-1");
  });
});
