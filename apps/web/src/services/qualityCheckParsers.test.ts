import { describe, expect, it } from "vitest";

import {
  QUALITY_ANNOTATION_ID,
  QUALITY_RUN_ID,
  QUALITY_SUBMISSION_ID,
  QUALITY_PROJECT_ID,
  qualityCheckRunFixture,
  qualityFindingFixture,
} from "./__fixtures__/qualityFixtures";
import {
  parseQualityCheckRun,
  parseQualityCheckRunPage,
  parseQualityFinding,
} from "./qualityCheckParsers";
import { ContractValidationError } from "./validation";

/** 契约形状的原始 JSON（snake_case，含三态 passed）。 */
function rawFinding(overrides: Record<string, unknown> = {}) {
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

function rawRun(overrides: Record<string, unknown> = {}) {
  return {
    run_id: QUALITY_RUN_ID,
    project_id: QUALITY_PROJECT_ID,
    submission_id: QUALITY_SUBMISSION_ID,
    rule_set_version: `sha256:${"b".repeat(64)}`,
    status: "PASSED",
    error_count: 0,
    warn_count: 0,
    started_at: "2026-11-17T08:00:00Z",
    finished_at: "2026-11-17T08:00:01Z",
    findings: [rawFinding()],
    created_at: "2026-11-17T08:00:01Z",
    created_by: null,
    ...overrides,
  };
}

describe("质量检查结果解析（三态 passed）", () => {
  it("passed=null 原样保留为 null，不被折叠成 false 或 true", () => {
    const finding = parseQualityFinding(rawFinding({ passed: null }));

    expect(finding.passed).toBeNull();
  });

  it("passed=false 保留为 false", () => {
    const finding = parseQualityFinding(rawFinding({ passed: false }));

    expect(finding.passed).toBe(false);
  });

  it("passed 缺失即契约违约：不静默当作「无结论」", () => {
    const raw = rawFinding();
    delete (raw as Record<string, unknown>).passed;

    expect(() => parseQualityFinding(raw)).toThrow(ContractValidationError);
    expect(() => parseQualityFinding(raw)).toThrow(/passed/);
  });

  it("passed 为非布尔值时报错", () => {
    expect(() => parseQualityFinding(rawFinding({ passed: "true" }))).toThrow(
      /布尔值或 null/,
    );
  });

  it("severity 只接受契约枚举 ERROR / WARN", () => {
    expect(() =>
      parseQualityFinding(rawFinding({ severity: "INFO" })),
    ).toThrow(/severity/);
    expect(
      parseQualityFinding(rawFinding({ severity: "WARN" })).severity,
    ).toBe("WARN");
  });
});

describe("质量检查记录解析", () => {
  it("解析完整 run，保留逐条 findings 与计数", () => {
    const run = parseQualityCheckRun(
      rawRun({
        status: "FAILED",
        error_count: 2,
        warn_count: 1,
        findings: [
          rawFinding({ passed: false }),
          rawFinding({ passed: null, severity: "WARN", rule_id: "boundary" }),
        ],
      }),
    );

    expect(run.run_id).toBe(QUALITY_RUN_ID);
    expect(run.status).toBe("FAILED");
    expect(run.error_count).toBe(2);
    expect(run.findings).toHaveLength(2);
    expect(run.findings[0]?.passed).toBe(false);
    expect(run.findings[1]?.passed).toBeNull();
  });

  it("后端未返回 updated_at / updated_by（契约必填）时仍可解析", () => {
    const run = parseQualityCheckRun(rawRun());

    expect(run.updated_at).toBeNull();
    expect(run.updated_by).toBeNull();
  });

  it("status 只接受契约枚举 PASSED / FAILED", () => {
    expect(() => parseQualityCheckRun(rawRun({ status: "RUNNING" }))).toThrow(
      /status/,
    );
  });

  it("findings 必须是数组", () => {
    expect(() => parseQualityCheckRun(rawRun({ findings: null }))).toThrow(
      /findings/,
    );
  });

  it("分页解析逐项校验，坏数据不会进入 UI", () => {
    const page = parseQualityCheckRunPage({
      items: [rawRun()],
      next_cursor: "cursor-2",
    });

    expect(page.items).toHaveLength(1);
    expect(page.next_cursor).toBe("cursor-2");
    expect(() =>
      parseQualityCheckRunPage({
        items: [rawRun({ run_id: "not-a-uuid" })],
        next_cursor: null,
      }),
    ).toThrow(/run_id/);
  });
});

describe("质量检查解析与展示类型一致", () => {
  it("fixture 能被解析器接受（fixture 与契约同步的回归守卫）", () => {
    const fixture = qualityCheckRunFixture({
      findings: [qualityFindingFixture({ passed: null })],
    });

    const parsed = parseQualityCheckRun(JSON.parse(JSON.stringify(fixture)));

    expect(parsed.findings[0]?.passed).toBeNull();
    expect(parsed.project_id).toBe(fixture.project_id);
  });
});
