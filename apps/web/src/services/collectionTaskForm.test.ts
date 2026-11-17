import { describe, expect, it } from "vitest";

import { buildTaskCreateInput } from "./collectionTaskForm";
import type { TaskCreateFormValues } from "./collectionTaskForm";

const base: TaskCreateFormValues = {
  code: " COL-001 ",
  name: " 厨房采集 ",
  priority: "P1",
  target_episode_count: 12,
  target_duration_sec: 3600,
};

describe("采集任务创建表单转换", () => {
  it("只提交契约字段，未选关联项整字段省略", () => {
    expect(buildTaskCreateInput(base)).toEqual({
      code: "COL-001",
      name: "厨房采集",
      priority: "P1",
      target_episode_count: 12,
      target_duration_sec: 3600,
    });
  });

  it("SOP 与动作库自动带出选中行的冻结版本", () => {
    const input = buildTaskCreateInput(
      {
        ...base,
        sop_id: "sop-1",
        action_spec_id: "spec-1",
        scenario_id: "scenario-1",
        plan_id: "plan-1",
        location_id: "loc-1",
      },
      { sop_version: "sop-kitchen-v3", action_spec_version: "v2" },
    );

    expect(input).toMatchObject({
      sop_id: "sop-1",
      sop_version: "sop-kitchen-v3",
      action_spec_id: "spec-1",
      action_spec_version: "v2",
      scenario_id: "scenario-1",
      plan_id: "plan-1",
      location_id: "loc-1",
    });
  });

  it("选了 SOP 却没有版本时拒绝提交（服务端要求成对）", () => {
    expect(() => buildTaskCreateInput({ ...base, sop_id: "sop-1" })).toThrow(
      /未取到其版本号/,
    );
  });

  it("只给版本没有 ID 同样拒绝", () => {
    expect(() =>
      buildTaskCreateInput(base, { action_spec_version: "v2" }),
    ).toThrow(/必须与动作库同时存在/);
  });

  it("空字符串关联项与空白 ID 视为未选择", () => {
    expect(
      buildTaskCreateInput({ ...base, plan_id: "   ", location_id: "" }),
    ).not.toHaveProperty("plan_id");
  });

  it("目标条数非整数或负数被拒绝", () => {
    expect(() =>
      buildTaskCreateInput({ ...base, target_episode_count: -1 }),
    ).toThrow(/不小于 0 的整数/);
    expect(() =>
      buildTaskCreateInput({ ...base, target_duration_sec: 1.5 }),
    ).toThrow(/不小于 0 的整数/);
  });

  it("优先级与必填文本按契约校验", () => {
    expect(() =>
      buildTaskCreateInput({ ...base, priority: "P9" as never }),
    ).toThrow(/优先级/);
    expect(() => buildTaskCreateInput({ ...base, name: "  " })).toThrow(
      /请填写名称/,
    );
    expect(() => buildTaskCreateInput({ ...base, code: "x".repeat(65) })).toThrow(
      /最多 64 字符/,
    );
  });
});
