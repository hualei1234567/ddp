import { describe, expect, it } from "vitest";

import { annotationTaskFixture } from "./__fixtures__/annotationFixtures";
import { parseAnnotationTask } from "./annotationParsers";

describe("服务端 allowed_actions 的可选解析", () => {
  it("字段存在时保留排序后的动作列表", () => {
    const parsed = parseAnnotationTask({
      ...annotationTaskFixture,
      allowed_actions: ["cancel", "pause", "submit"],
    });
    expect(parsed.allowed_actions).toEqual(["cancel", "pause", "submit"]);
  });

  it("字段缺失时解析结果与旧契约一致", () => {
    const parsed = parseAnnotationTask(annotationTaskFixture);
    expect(parsed.allowed_actions).toBeUndefined();
    expect(parsed.task_id).toBe(annotationTaskFixture.task_id);
  });

  it("字段不是字符串数组时拒绝进入 UI", () => {
    expect(() =>
      parseAnnotationTask({
        ...annotationTaskFixture,
        allowed_actions: "pause",
      }),
    ).toThrow(/allowed_actions/);
  });
});
