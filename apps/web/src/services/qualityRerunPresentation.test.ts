import { describe, expect, it } from "vitest";

import { filterFailedSubjects } from "./qualityRerunPresentation";

describe("失败项重试筛选（FR-028）", () => {
  it("只保留上一次结论为 FAILED 的对象", () => {
    expect(
      filterFailedSubjects([
        { subjectId: "e1", lastStatus: "FAILED" },
        { subjectId: "e2", lastStatus: "PASSED" },
        { subjectId: "e3", lastStatus: "INCOMPLETE" },
        { subjectId: "e4", lastStatus: null },
      ]),
    ).toEqual([{ subjectId: "e1", lastStatus: "FAILED" }]);
  });

  it("全部不是 FAILED 时返回空列表（页面据此禁用确认）", () => {
    expect(
      filterFailedSubjects([
        { subjectId: "e1", lastStatus: "PASSED" },
        { subjectId: "e2", lastStatus: null },
      ]),
    ).toEqual([]);
  });
});
