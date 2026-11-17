/** 质量管理（N4 质量检查）测试数据：字段与契约 `schemas.stage9.yaml` 一一对应。 */

import type { QualityCheckRun, QualityFinding } from "../../types/quality";

export const QUALITY_PROJECT_ID = "77777777-7777-7777-8777-777777777777";
export const QUALITY_SUBMISSION_ID = "88888888-8888-8888-8888-888888888888";
export const QUALITY_RUN_ID = "99999999-9999-9999-8999-999999999999";
export const QUALITY_ANNOTATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

export function qualityFindingFixture(
  overrides: Partial<QualityFinding> = {},
): QualityFinding {
  return {
    annotation_id: QUALITY_ANNOTATION_ID,
    rule_id: "label.required",
    rule_version: 1,
    severity: "ERROR",
    passed: true,
    message: "必填标签齐全",
    ...overrides,
  };
}

export function qualityCheckRunFixture(
  overrides: Partial<QualityCheckRun> = {},
): QualityCheckRun {
  return {
    run_id: QUALITY_RUN_ID,
    project_id: QUALITY_PROJECT_ID,
    submission_id: QUALITY_SUBMISSION_ID,
    rule_set_version: `sha256:${"a".repeat(64)}`,
    status: "PASSED",
    error_count: 0,
    warn_count: 0,
    started_at: "2026-11-17T08:00:00Z",
    finished_at: "2026-11-17T08:00:01Z",
    findings: [qualityFindingFixture()],
    created_at: "2026-11-17T08:00:01Z",
    created_by: null,
    ...overrides,
  };
}
