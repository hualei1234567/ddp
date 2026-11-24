/**
 * 统一质量运行（N4-004）的测试夹具。
 *
 * 默认值刻意是"六类齐全、全部 PASS"：任何测试想表达"无结论/不适用"，
 * 必须显式覆盖，避免夹具本身把缺失类别伪装成通过。
 */

import {
  QUALITY_CATEGORY_VALUES,
  type QualityCategory,
  type QualityCategoryStatus,
  type QualityCategorySummary,
  type QualityRun,
  type QualityRunFinding,
} from "../../types/qualityRun";

export const QUALITY_RUN_ID = "11111111-1111-4111-8111-111111111111";
export const QUALITY_PROJECT_ID = "22222222-2222-4222-8222-222222222222";
export const QUALITY_IMPORT_ID = "33333333-3333-4333-8333-333333333333";
export const QUALITY_CREATED_BY = "44444444-4444-4444-8444-444444444444";
export const QUALITY_FINDING_ID = "55555555-5555-4555-8555-555555555555";
export const QUALITY_RULE_SET_VERSION = `sha256:${"0".repeat(64)}`;

export function qualityCategorySummaryFixture(
  category: QualityCategory,
  overrides: Partial<QualityCategorySummary> = {},
): QualityCategorySummary {
  return {
    category,
    status: "PASS",
    finding_count: 1,
    pass_count: 1,
    fail_count: 0,
    not_applicable_count: 0,
    error_count: 0,
    score: 1,
    ...overrides,
  };
}

export function qualityRunFixture(
  overrides: Partial<QualityRun> = {},
): QualityRun {
  return {
    run_id: QUALITY_RUN_ID,
    project_id: QUALITY_PROJECT_ID,
    subject_type: "DATASOURCE_IMPORT",
    subject_id: QUALITY_IMPORT_ID,
    rule_set_version: QUALITY_RULE_SET_VERSION,
    status: "PASSED",
    score: 1,
    finding_count: 6,
    error_count: 0,
    warn_count: 0,
    categories: QUALITY_CATEGORY_VALUES.map((category) =>
      qualityCategorySummaryFixture(category),
    ),
    started_at: "2026-11-18T08:00:00Z",
    finished_at: "2026-11-18T08:00:01Z",
    created_at: "2026-11-18T08:00:01Z",
    created_by: QUALITY_CREATED_BY,
    ...overrides,
  };
}

/** 指定某类别的聚合状态；其余类别保持夹具默认。 */
export function qualityRunWithCategoryStatus(
  category: QualityCategory,
  status: QualityCategoryStatus,
  score: number | null = null,
): QualityRun {
  return qualityRunFixture({
    categories: QUALITY_CATEGORY_VALUES.map((item) =>
      item === category
        ? qualityCategorySummaryFixture(item, {
            status,
            pass_count: status === "PASS" ? 1 : 0,
            fail_count: status === "FAIL" ? 1 : 0,
            not_applicable_count: status === "NOT_APPLICABLE" ? 1 : 0,
            score,
          })
        : qualityCategorySummaryFixture(item),
    ),
  });
}

export function qualityRunFindingFixture(
  overrides: Partial<QualityRunFinding> = {},
): QualityRunFinding {
  return {
    finding_id: QUALITY_FINDING_ID,
    run_id: QUALITY_RUN_ID,
    category: "INTEGRITY",
    rule_id: "assets_present",
    rule_version: "1.1.0",
    severity: "ERROR",
    status: "PASS",
    subject_ref: "",
    message: "共 39 条媒体资产",
    evidence: null,
    evidence_location: null,
    score: 1,
    created_at: "2026-11-18T08:00:01Z",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// V31-033 / FR-026..028：批量重跑与差异报告夹具
// ---------------------------------------------------------------------------

import type {
  QualityFindingDiff,
  QualityDiffKind,
  QualityDiffStatus,
  QueuedQualityRun,
  QualityRunDiffReport,
} from "../../types/qualityRun";

export const QUALITY_EPISODE_ID = "66666666-6666-4666-8666-666666666666";
/** 第二个 Episode 夹具；批量重跑测试用它表达「多个对象」。 */
export const QUALITY_OTHER_EPISODE_ID_FIXTURE =
  "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const QUALITY_BASELINE_RUN_ID =
  "77777777-7777-4777-8777-777777777777";
export const QUALITY_CANDIDATE_RUN_ID =
  "88888888-8888-4888-8888-888888888888";

/** 排队回执：只描述排队事实，刻意没有 status / score / 六类聚合。 */
export function queuedQualityRunFixture(
  overrides: Partial<QueuedQualityRun> = {},
): QueuedQualityRun {
  return {
    run_id: QUALITY_RUN_ID,
    project_id: QUALITY_PROJECT_ID,
    subject_type: "EPISODE",
    subject_id: QUALITY_EPISODE_ID,
    rule_set_version: QUALITY_RULE_SET_VERSION,
    lifecycle: "QUEUED",
    max_attempts: 1,
    created_at: "2026-11-19T08:00:00Z",
    created_by: QUALITY_CREATED_BY,
    ...overrides,
  };
}

/** 单条差异；两侧结论默认与 kind 匹配（NEW 只有 candidate 等）。 */
export function qualityFindingDiffFixture(
  kind: QualityDiffKind,
  overrides: Partial<QualityFindingDiff> = {},
): QualityFindingDiff {
  const statuses: Record<QualityDiffKind, [QualityDiffStatus | null, QualityDiffStatus | null]> = {
    NEW: [null, "FAIL"],
    RESOLVED: ["FAIL", null],
    UNCHANGED: ["FAIL", "FAIL"],
    CHANGED: ["FAIL", "PASS"],
  };
  const [baseline, candidate] = statuses[kind];
  return {
    rule_id: "assets_present",
    subject_ref: "",
    kind,
    baseline_status: baseline,
    candidate_status: candidate,
    ...overrides,
  };
}

/** 四类差异各一条；计数与 findings 严格一致（解析层校验该一致性）。 */
export function qualityRunDiffFixture(
  overrides: Partial<QualityRunDiffReport> = {},
): QualityRunDiffReport {
  const findings = [
    qualityFindingDiffFixture("NEW"),
    qualityFindingDiffFixture("RESOLVED"),
    qualityFindingDiffFixture("UNCHANGED"),
    qualityFindingDiffFixture("CHANGED"),
  ];
  return {
    baseline_run_id: QUALITY_BASELINE_RUN_ID,
    candidate_run_id: QUALITY_CANDIDATE_RUN_ID,
    findings,
    new_count: 1,
    resolved_count: 1,
    unchanged_count: 1,
    changed_count: 1,
    has_regression: true,
    ...overrides,
  };
}
