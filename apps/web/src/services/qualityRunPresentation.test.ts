import { describe, expect, it } from "vitest";

import { qualityRunFindingFixture } from "./__fixtures__/qualityRunFixtures";
import {
  QUALITY_FILTER_ALL,
  filterFindingsBySeverity,
  findingSubjectLabel,
  formatQualityScore,
  presentCategoryStatus,
  presentFindingStatus,
  presentRunStatus,
  readRuleCategoryFilter,
  readSeverityFilter,
} from "./qualityRunPresentation";

describe("统一质量状态映射", () => {
  it("INCOMPLETE 不映射成通过，使用非成功语气", () => {
    const presentation = presentRunStatus("INCOMPLETE");

    expect(presentation.label).toContain("INCOMPLETE");
    expect(presentation.label).toContain("无结论");
    expect(presentation.label).not.toContain("PASSED");
    expect(presentation.tone).toBe("warning");
  });

  it("FAILED 映射为阻断，PASSED 不暗示所有规则通过", () => {
    expect(presentRunStatus("FAILED").tone).toBe("error");
    const passed = presentRunStatus("PASSED");
    expect(passed.label).toContain("无阻断");
    expect(passed.label).not.toContain("全部");
  });

  it("NOT_APPLICABLE 是中性无结论，不是通过", () => {
    const presentation = presentCategoryStatus("NOT_APPLICABLE");

    expect(presentation.tone).toBe("neutral");
    expect(presentation.label).toContain("不适用");
    expect(presentation.label).toContain("无结论");
  });

  it("只有 PASS 使用成功语气", () => {
    expect(presentCategoryStatus("PASS").tone).toBe("success");
    expect(presentCategoryStatus("WARN").tone).toBe("warning");
    expect(presentCategoryStatus("ERROR").tone).toBe("error");
  });

  it("单条结论：NOT_APPLICABLE 与 ERROR 都不显示为通过", () => {
    expect(presentFindingStatus("PASS").label).toBe("通过");
    expect(presentFindingStatus("FAIL").label).toBe("未通过");
    expect(presentFindingStatus("NOT_APPLICABLE").label).toBe("无结论");
    expect(presentFindingStatus("ERROR").label).toContain("执行失败");
    expect(presentFindingStatus("ERROR").label).toContain("无结论");
  });
});

describe("评分与筛选映射", () => {
  it("null 评分显示为无可用评分，不显示 0", () => {
    expect(formatQualityScore(null)).toContain("无结论");
    expect(formatQualityScore(null)).not.toContain("0.00");
    expect(formatQualityScore(0)).toBe("0.00");
    expect(formatQualityScore(0.5)).toBe("0.50");
  });

  it("严重度筛选只返回匹配项，null 返回全部副本", () => {
    const findings = [
      qualityRunFindingFixture({ severity: "ERROR" }),
      qualityRunFindingFixture({
        finding_id: "66666666-6666-4666-8666-666666666666",
        severity: "WARN",
      }),
    ];

    expect(filterFindingsBySeverity(findings, "WARN")).toHaveLength(1);
    expect(filterFindingsBySeverity(findings, "WARN")[0]?.severity).toBe("WARN");
    expect(filterFindingsBySeverity(findings, null)).toEqual(findings);
    expect(filterFindingsBySeverity(findings, null)).not.toBe(findings);
  });

  it("类别筛选不接受派生 OVERALL，避免发出返回空列表的请求", () => {
    expect(readRuleCategoryFilter("INTEGRITY")).toBe("INTEGRITY");
    expect(readRuleCategoryFilter("OVERALL")).toBeNull();
    expect(readRuleCategoryFilter(QUALITY_FILTER_ALL)).toBeNull();
  });

  it("严重度筛选值只接受闭集", () => {
    expect(readSeverityFilter("WARN")).toBe("WARN");
    expect(readSeverityFilter("INFO")).toBeNull();
    expect(readSeverityFilter(QUALITY_FILTER_ALL)).toBeNull();
  });

  it("对象级结论的 subject_ref 为空字符串时显示「对象级」", () => {
    expect(findingSubjectLabel(qualityRunFindingFixture())).toBe("对象级");
    expect(
      findingSubjectLabel(
        qualityRunFindingFixture({ subject_ref: "episode-A" }),
      ),
    ).toBe("episode-A");
  });
});
