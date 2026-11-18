import { describe, expect, it } from "vitest";

import {
  buildAnnotationTaskCreateInput,
  buildReviewPolicy,
  canEnableAutoApprove,
  type AnnotationTaskFormOptions,
  type AnnotationTaskFormValues,
} from "./annotationTaskForm";

const base: AnnotationTaskFormValues = {
  code: " AN-TASK-001 ",
  name: " 双标任务 ",
  sample_set_id: "11111111-1111-7111-8111-111111111111",
  schema_id: "22222222-2222-7222-8222-222222222222",
  target_item_count: 10,
  target_episode_count: 5,
  priority: "P1",
  assignment_mode: "MANUAL",
  review_mode: "SINGLE",
};

const options: AnnotationTaskFormOptions = {
  schemaVersion: 3,
  canAutoApprove: true,
};

describe("标注任务创建表单映射", () => {
  it("默认单人任务只写 review_mode，不提交阈值与自动通过", () => {
    const input = buildAnnotationTaskCreateInput(base, options);

    expect(input).toMatchObject({
      code: "AN-TASK-001",
      name: "双标任务",
      schema_version: 3,
      target_item_count: 10,
      target_episode_count: 5,
      priority: "P1",
      assignment_mode: "MANUAL",
    });
    expect(input.iaa_policy).toEqual({ review_mode: "SINGLE" });
    // 双标配置只走既有 iaa_policy，不再重复提交顶层便捷字段。
    expect(input).not.toHaveProperty("review_mode");
    expect(input).not.toHaveProperty("auto_approve");
    expect(input).not.toHaveProperty("agreement_threshold");
  });

  it("双标任务把百分数阈值换算为契约要求的 0–1", () => {
    const input = buildAnnotationTaskCreateInput(
      {
        ...base,
        review_mode: "DOUBLE",
        agreement_threshold_percent: 85.5,
      },
      options,
    );

    expect(input.iaa_policy).toEqual({
      review_mode: "DOUBLE",
      agreement_threshold: 0.855,
    });
  });

  it("阈值留空时不提交，由服务端默认策略决定", () => {
    const policy = buildReviewPolicy({ review_mode: "DOUBLE" }, true);

    expect(policy).toEqual({ review_mode: "DOUBLE" });
  });

  it("阈值超出 0–100 或非有限数被拒绝", () => {
    for (const value of [-1, 100.1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(() =>
        buildReviewPolicy(
          { review_mode: "DOUBLE", agreement_threshold_percent: value },
          true,
        ),
      ).toThrow(/0 到 100/);
    }
  });

  it("自动通过默认关闭：未勾选时不写入 iaa_policy", () => {
    for (const autoApprove of [undefined, false]) {
      expect(
        buildReviewPolicy({ review_mode: "DOUBLE", auto_approve: autoApprove }, true),
      ).toEqual({ review_mode: "DOUBLE" });
    }
  });

  it("有权限且显式勾选时才写入 auto_approve", () => {
    expect(
      buildReviewPolicy({ review_mode: "DOUBLE", auto_approve: true }, true),
    ).toEqual({ review_mode: "DOUBLE", auto_approve: true });
  });

  it("无权限时拒绝开启自动通过（服务端 403 的前端防线）", () => {
    expect(() =>
      buildReviewPolicy({ review_mode: "DOUBLE", auto_approve: true }, false),
    ).toThrow(/项目负责人/);
  });

  it("单人任务不允许自动通过，与服务端规则一致", () => {
    expect(() =>
      buildReviewPolicy({ review_mode: "SINGLE", auto_approve: true }, true),
    ).toThrow(/仅适用于双标/);
  });

  it("角色提示与服务端口径一致：负责人与平台管理员可开启", () => {
    expect(canEnableAutoApprove(null)).toBe(true);
    expect(canEnableAutoApprove("owner")).toBe(true);
    expect(canEnableAutoApprove("editor")).toBe(false);
    expect(canEnableAutoApprove("viewer")).toBe(false);
  });

  it("截止时间空白提交 null，非法格式被拒绝", () => {
    expect(
      buildAnnotationTaskCreateInput({ ...base, due_at: "   " }, options).due_at,
    ).toBeNull();
    expect(() =>
      buildAnnotationTaskCreateInput({ ...base, due_at: "不是日期" }, options),
    ).toThrow(/合法的日期时间/);
  });

  it("必填项、整数项与优先级按契约校验", () => {
    expect(() =>
      buildAnnotationTaskCreateInput({ ...base, code: "  " }, options),
    ).toThrow(/任务编码/);
    expect(() =>
      buildAnnotationTaskCreateInput({ ...base, target_item_count: 0 }, options),
    ).toThrow(/不小于 1 的整数/);
    expect(() =>
      buildAnnotationTaskCreateInput({ ...base, target_episode_count: 1.5 }, options),
    ).toThrow(/不小于 1 的整数/);
    expect(() =>
      buildAnnotationTaskCreateInput(
        { ...base, priority: "P9" as never },
        options,
      ),
    ).toThrow(/优先级/);
  });
});
