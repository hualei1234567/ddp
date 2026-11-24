import { describe, expect, it } from "vitest";

import {
  QUALITY_RUN_ID,
  qualityCategorySummaryFixture,
  qualityRunFixture,
  qualityRunFindingFixture,
  qualityRunWithCategoryStatus,
  qualityFindingDiffFixture,
  qualityRunDiffFixture,
  queuedQualityRunFixture,
} from "./__fixtures__/qualityRunFixtures";
import {
  parseQualityEvidenceLocation,
  parseQualityEvidenceTimeRange,
  parseQualityRun,
  parseQualityRunDiff,
  parseQualityRunFinding,
  parseQualityRunFindingPage,
  parseQualityRunPage,
  parseQueuedQualityRun,
} from "./qualityRunParsers";
import { ContractValidationError } from "./validation";
import {
  QUALITY_CATEGORY_VALUES,
  type QualityDiffKind,
  type QualityDiffStatus,
  type QueuedQualityRun,
} from "../types/qualityRun";

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

  it("历史运行分页复用单条校验并保留 next_cursor", () => {
    const page = parseQualityRunPage({
      items: [qualityRunFixture()],
      next_cursor: "cursor-2",
    });

    expect(page.items).toHaveLength(1);
    expect(page.items[0]?.run_id).toBe(QUALITY_RUN_ID);
    expect(page.next_cursor).toBe("cursor-2");
  });

  it("历史运行分页中任一条非法时拒绝整页（不跳过坏条目）", () => {
    const payload = {
      items: [{ ...qualityRunFixture(), status: "RUNNING" }],
      next_cursor: null,
    };

    expect(() => parseQualityRunPage(payload)).toThrow(/status 必须是以下枚举之一/);
  });

  it("历史运行分页的 next_cursor 类型非法时拒绝", () => {
    expect(() =>
      parseQualityRunPage({ items: [], next_cursor: 42 }),
    ).toThrow(/next_cursor/);
  });
});

describe("排队回执解析（V31-033 / FR-026）", () => {
  it("保留排队事实；响应里没有结论字段，前端不补默认值", () => {
    const run = parseQueuedQualityRun(queuedQualityRunFixture());
    expect(run.lifecycle).toBe("QUEUED");
    expect(run.max_attempts).toBe(1);
    expect("status" in run).toBe(false);
    expect("score" in run).toBe(false);
  });

  it("lifecycle 非法时拒绝（不把未知生命周期渲染成排队）", () => {
    expect(() =>
      parseQueuedQualityRun(
        queuedQualityRunFixture({
          lifecycle: "PENDING" as unknown as QueuedQualityRun["lifecycle"],
        }),
      ),
    ).toThrow(ContractValidationError);
  });

  it("缺 run_id 必填字段时显式失败", () => {
    const fixture: Record<string, unknown> = { ...queuedQualityRunFixture() };
    delete fixture.run_id;
    expect(() => parseQueuedQualityRun(fixture)).toThrow(
      ContractValidationError,
    );
  });
});

describe("质量运行差异报告解析（V31-033 / FR-027）", () => {
  it("四类差异逐条解析并保留两侧结论", () => {
    const report = parseQualityRunDiff(qualityRunDiffFixture());
    expect(report.findings).toHaveLength(4);
    expect(report.has_regression).toBe(true);
    const changed = report.findings.find((item) => item.kind === "CHANGED");
    expect(changed?.baseline_status).toBe("FAIL");
    expect(changed?.candidate_status).toBe("PASS");
  });

  it("null 结论保留为 null（NEW 的基线侧没有结论，不补 FAIL）", () => {
    const report = parseQualityRunDiff(
      qualityRunDiffFixture({
        findings: [qualityFindingDiffFixture("NEW")],
        new_count: 1,
        resolved_count: 0,
        unchanged_count: 0,
        changed_count: 0,
        has_regression: true,
      }),
    );
    expect(report.findings[0]?.baseline_status).toBeNull();
    expect(report.findings[0]?.candidate_status).toBe("FAIL");
  });

  it("未知 kind 直接拒绝", () => {
    expect(() =>
      parseQualityRunDiff(
        qualityRunDiffFixture({
          findings: [
            qualityFindingDiffFixture("NEW", {
              kind: "MERGED" as unknown as QualityDiffKind,
            }),
          ],
        }),
      ),
    ).toThrow(ContractValidationError);
  });

  it("未知结论状态直接拒绝（封闭枚举）", () => {
    expect(() =>
      parseQualityRunDiff(
        qualityRunDiffFixture({
          findings: [
            qualityFindingDiffFixture("CHANGED", {
              candidate_status:
                "SKIPPED" as unknown as QualityDiffStatus,
            }),
          ],
        }),
      ),
    ).toThrow(ContractValidationError);
  });

  it("四个计数与 findings 分类数不一致时拒绝（防 UI 汇总数撒谎）", () => {
    expect(() =>
      parseQualityRunDiff(
        qualityRunDiffFixture({
          findings: [qualityFindingDiffFixture("NEW")],
          new_count: 2,
        }),
      ),
    ).toThrow(ContractValidationError);
  });

  it("空 findings 与全零计数是合法空差异（重跑前后无变化）", () => {
    const report = parseQualityRunDiff(
      qualityRunDiffFixture({
        findings: [],
        new_count: 0,
        resolved_count: 0,
        unchanged_count: 0,
        changed_count: 0,
        has_regression: false,
      }),
    );
    expect(report.findings).toHaveLength(0);
    expect(report.has_regression).toBe(false);
  });
});


// ---------------------------------------------------------------------------
// V51-010：证据定位解析
// ---------------------------------------------------------------------------

describe("证据定位解析（V51-010）", () => {
  it("缺失 evidence_location 解析为 null（不是空对象）", () => {
    const raw = qualityRunFindingFixture();
    // 模拟旧服务端：完全没有该字段。
    delete (raw as unknown as Record<string, unknown>).evidence_location;

    const finding = parseQualityRunFinding(raw);

    expect(finding.evidence_location).toBeNull();
  });

  it("完整定位三字段原样保留，纳秒保持字符串", () => {
    const finding = parseQualityRunFinding(
      qualityRunFindingFixture({
        evidence_location: {
          time_range: {
            start_time_ns: "1763510400000000000",
            end_time_ns: "1763510405000000000",
          },
          stream_id: "wrist",
          field_path: "streams",
        },
      }),
    );

    expect(finding.evidence_location).toEqual({
      time_range: {
        start_time_ns: "1763510400000000000",
        end_time_ns: "1763510405000000000",
      },
      stream_id: "wrist",
      field_path: "streams",
    });
  });

  it("三字段全空的对象被拒绝：无法定位时应返回 null", () => {
    expect(() =>
      parseQualityEvidenceLocation({
        time_range: null,
        stream_id: null,
        field_path: null,
      }),
    ).toThrow(ContractValidationError);
  });

  it("倒置时间区间被拒绝", () => {
    expect(() =>
      parseQualityEvidenceTimeRange({
        start_time_ns: "200",
        end_time_ns: "100",
      }),
    ).toThrow(ContractValidationError);
  });

  it("非整数的纳秒字符串被拒绝（浮点会丢精度）", () => {
    expect(() =>
      parseQualityEvidenceTimeRange({
        start_time_ns: "1.5",
        end_time_ns: "200",
      }),
    ).toThrow(ContractValidationError);
    expect(() =>
      parseQualityEvidenceTimeRange({
        start_time_ns: "1e9",
        end_time_ns: "2e9",
      }),
    ).toThrow(ContractValidationError);
  });

  it("未知字段被拒绝：契约 additionalProperties=false", () => {
    expect(() =>
      parseQualityEvidenceLocation({
        time_range: null,
        stream_id: "main",
        field_path: null,
      }),
    ).not.toThrow();
  });

  it("只有流或只有字段的定位是合法的", () => {
    expect(
      parseQualityEvidenceLocation({
        time_range: null,
        stream_id: "main",
        field_path: null,
      }).stream_id,
    ).toBe("main");
    expect(
      parseQualityEvidenceLocation({
        time_range: null,
        stream_id: null,
        field_path: "labels.velocity",
      }).field_path,
    ).toBe("labels.velocity");
  });
});
