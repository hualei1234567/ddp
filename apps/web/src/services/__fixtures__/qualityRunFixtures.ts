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
    score: 1,
    created_at: "2026-11-18T08:00:01Z",
    ...overrides,
  };
}
