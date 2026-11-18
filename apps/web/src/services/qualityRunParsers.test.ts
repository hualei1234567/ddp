import { describe, expect, it } from "vitest";

import {
  QUALITY_RUN_ID,
  qualityCategorySummaryFixture,
  qualityRunFixture,
  qualityRunFindingFixture,
  qualityRunWithCategoryStatus,
} from "./__fixtures__/qualityRunFixtures";
import {
  parseQualityRun,
  parseQualityRunFinding,
  parseQualityRunFindingPage,
} from "./qualityRunParsers";
import { ContractValidationError } from "./validation";
import { QUALITY_CATEGORY_VALUES } from "../types/qualityRun";

describe("统一质量运行解析", () => {
  it("六类齐全时保留服务端顺序与 null 评分", () => {
    const run = parseQualityRun(
      qualityRunWithCategoryStatus("ANNOTATION", "NOT_APPLICABLE", null),
    );

    expect(run.categories).toHaveLength(6);
    expect(run.categories.map((item) => item.category)).toEqual([
      "INTEGRITY",
      "TEMPORAL",
      "VISUAL",
      "METADATA",
      "ANNOTATION",
      "OVERALL",
    ]);
    const annotation = run.categories.find(
      (item) => item.category === "ANNOTATION",
    );
    expect(annotation?.status).toBe("NOT_APPLICABLE");
    expect(annotation?.score).toBeNull();
    expect(run.status).toBe("PASSED");
  });

  it("INCOMPLETE 运行原样保留，不被改写成 PASSED", () => {
    const run = parseQualityRun(
      qualityRunFixture({ status: "INCOMPLETE", score: null }),
    );

    expect(run.status).toBe("INCOMPLETE");
    expect(run.score).toBeNull();
  });

  it("缺少某一类时拒绝整份响应（不补 PASS）", () => {
    const payload = qualityRunFixture();
    payload.categories = payload.categories.slice(0, 5);

    expect(() => parseQualityRun(payload)).toThrowError(ContractValidationError);
    expect(() => parseQualityRun(payload)).toThrow(/必须恰好包含 6 类/);
  });

  it("六类顺序错误时拒绝（避免 UI 把类别标签贴到别的结论上）", () => {
    const payload = qualityRunFixture();
    payload.categories = [
      qualityCategorySummaryFixture("TEMPORAL"),
      qualityCategorySummaryFixture("INTEGRITY"),
      ...QUALITY_CATEGORY_VALUES.slice(2).map((category) =>
        qualityCategorySummaryFixture(category),
      ),
    ];

    expect(() => parseQualityRun(payload)).toThrow(/六类顺序固定/);
  });

  it("未知运行状态直接拒绝", () => {
    const payload = {
      ...qualityRunFixture(),
      status: "RUNNING",
    };

    expect(() => parseQualityRun(payload)).toThrow(/status 必须是以下枚举之一/);
  });

  it("rule_set_version 必须是内容 SHA", () => {
    const payload = { ...qualityRunFixture(), rule_set_version: "1.1.0" };

    expect(() => parseQualityRun(payload)).toThrow(/sha256:<64 位小写十六进制>/);
  });

  it("score 超出 0..1 直接拒绝", () => {
    const payload = { ...qualityRunFixture(), score: 1.5 };

    expect(() => parseQualityRun(payload)).toThrow(/必须落在 0..1/);
  });
});

describe("统一质量结论解析", () => {
  it("NOT_APPLICABLE 的 score 为 null 时保留，不补成 0 或 1", () => {
    const finding = parseQualityRunFinding(
      qualityRunFindingFixture({
        status: "NOT_APPLICABLE",
        score: null,
        message: "该 Episode 没有可评估的导入媒体资产",
      }),
    );

    expect(finding.status).toBe("NOT_APPLICABLE");
    expect(finding.score).toBeNull();
    expect(finding.evidence).toBeNull();
  });

  it("ERROR（规则执行失败）保留为独立状态", () => {
    const finding = parseQualityRunFinding(
      qualityRunFindingFixture({ status: "ERROR", score: null }),
    );

    expect(finding.status).toBe("ERROR");
    expect(finding.score).toBeNull();
  });

  it("evidence 为对象时逐字段保留", () => {
    const finding = parseQualityRunFinding(
      qualityRunFindingFixture({
        evidence: { reason: "episode_has_no_import_media_assets" },
      }),
    );

    expect(finding.evidence).toEqual({
      reason: "episode_has_no_import_media_assets",
    });
  });

  it("未知结论状态直接拒绝", () => {
    expect(() =>
      parseQualityRunFinding(
        qualityRunFindingFixture({ status: "UNKNOWN" as never }),
      ),
    ).toThrow(/status 必须是以下枚举之一/);
  });

  it("分页解析逐项校验并保留 next_cursor", () => {
    const page = parseQualityRunFindingPage({
      items: [qualityRunFindingFixture()],
      next_cursor: "cursor-1",
    });

    expect(page.items).toHaveLength(1);
    expect(page.items[0]?.run_id).toBe(QUALITY_RUN_ID);
    expect(page.next_cursor).toBe("cursor-1");
  });
});
