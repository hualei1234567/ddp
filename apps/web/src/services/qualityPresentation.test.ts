import { describe, expect, it } from "vitest";

import { qualityFindingFixture } from "./__fixtures__/qualityFixtures";
import {
  GOLD_SET_ACTIONS,
  isWholeRunInconclusive,
  presentCheckStatus,
  presentFindingResult,
  presentGoldSetAction,
  presentRuleSeverity,
  summarizeFindings,
} from "./qualityPresentation";

describe("三态判定展示", () => {
  it("passed=true 显示「通过」", () => {
    expect(presentFindingResult(true)).toEqual({
      label: "通过",
      tone: "success",
    });
  });

  it("passed=false 显示「未通过」", () => {
    expect(presentFindingResult(false)).toEqual({
      label: "未通过",
      tone: "error",
    });
  });

  it("passed=null 显示「无结论」，且不是 success 语气", () => {
    const presentation = presentFindingResult(null);

    expect(presentation.label).toBe("无结论");
    expect(presentation.label).not.toContain("通过");
    expect(presentation.tone).not.toBe("success");
  });
});

describe("规则结果统计", () => {
  it("三类结论分别计数，null 不计入通过", () => {
    const summary = summarizeFindings([
      qualityFindingFixture({ passed: true }),
      qualityFindingFixture({ passed: true }),
      qualityFindingFixture({ passed: false }),
      qualityFindingFixture({ passed: null }),
    ]);

    expect(summary).toEqual({
      passedCount: 2,
      failedCount: 1,
      inconclusiveCount: 1,
      total: 4,
    });
  });

  it("空结果集不产生任何计数", () => {
    expect(summarizeFindings([])).toEqual({
      passedCount: 0,
      failedCount: 0,
      inconclusiveCount: 0,
      total: 0,
    });
  });

  it("全部无结论时标记为「本次没有结论」", () => {
    const summary = summarizeFindings([
      qualityFindingFixture({ passed: null }),
      qualityFindingFixture({ passed: null }),
    ]);

    expect(isWholeRunInconclusive(summary)).toBe(true);
  });

  it("存在通过或有违规时不标记为「本次没有结论」", () => {
    expect(
      isWholeRunInconclusive(
        summarizeFindings([qualityFindingFixture({ passed: true })]),
      ),
    ).toBe(false);
    expect(
      isWholeRunInconclusive(
        summarizeFindings([qualityFindingFixture({ passed: false })]),
      ),
    ).toBe(false);
    // 空结果集不是「没有结论」，是「没有规则结果」。
    expect(isWholeRunInconclusive(summarizeFindings([]))).toBe(false);
  });
});

describe("整体结论文案", () => {
  it("PASSED 不表述成「合格」", () => {
    const presentation = presentCheckStatus("PASSED");

    expect(presentation.label).toBe("无 ERROR 级违规");
    expect(presentation.label).not.toContain("合格");
    expect(presentation.tone).not.toBe("success");
  });

  it("FAILED 明确表示存在 ERROR 级违规", () => {
    expect(presentCheckStatus("FAILED")).toEqual({
      label: "存在 ERROR 级违规",
      tone: "error",
    });
  });
});

describe("规则级别展示", () => {
  it("ERROR 标明阻断、WARN 标明提醒", () => {
    expect(presentRuleSeverity("ERROR").label).toContain("阻断");
    expect(presentRuleSeverity("WARN").label).toContain("提醒");
  });
});

describe("黄金集动作展示", () => {
  it("动作集合与契约声明的 5 个动作一致", () => {
    expect(GOLD_SET_ACTIONS.map((item) => item.action)).toEqual([
      "submit-review",
      "return-draft",
      "activate",
      "retire",
      "revoke",
    ]);
  });

  it("已知动作有中文标签，未知动作原样展示", () => {
    expect(presentGoldSetAction("activate")).toBe("启用");
    expect(presentGoldSetAction("reopen")).toBe("reopen");
  });
});
