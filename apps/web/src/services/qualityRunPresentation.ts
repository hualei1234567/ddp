/**
 * 统一质量运行（N4-004 六类分层）的展示映射。
 *
 * 只做"服务端值怎么显示"，不复制任何判定：
 * - `PASS` / `FAIL` / `WARN` / `ERROR` / `NOT_APPLICABLE` 原样映射；
 * - `INCOMPLETE` 显式表达为"无结论"，绝不渲染成通过；
 * - `score === null` 显示为"无可用评分"，不显示成 0。
 */

import {
  QUALITY_RULE_CATEGORY_VALUES,
  QUALITY_SEVERITY_VALUES,
  type QualityCategory,
  type QualityCategoryStatus,
  type QualityFindingStatus,
  type QualityRuleCategory,
  type QualityRun,
  type QualityRunFinding,
  type QualityRunStatus,
  type QualitySeverity,
} from "../types/qualityRun";

export type QualityTone = "success" | "error" | "neutral" | "warning";

export interface QualityPresentation {
  label: string;
  tone: QualityTone;
}

export const QUALITY_CATEGORY_LABELS: Record<QualityCategory, string> = {
  INTEGRITY: "完整性（INTEGRITY）",
  TEMPORAL: "时序（TEMPORAL）",
  VISUAL: "视觉（VISUAL）",
  METADATA: "元数据（METADATA）",
  ANNOTATION: "标注（ANNOTATION）",
  OVERALL: "总体（OVERALL）",
};

export const INCOMPLETE_RUN_HINT =
  "INCOMPLETE 表示本次运行没有任何可判定结论：既不是通过，也不是失败，不得当作 PASSED 使用。";

export const NOT_APPLICABLE_HINT =
  "NOT_APPLICABLE 表示该类没有可判定结论（缺上下文或规则不适用于该对象），不代表通过。";

export const PASSED_SCOPE_HINT =
  "PASSED 只表示没有 ERROR 级阻断违规；WARN 级提醒与无结论项仍可能存在。";

/** 运行整体结论。`INCOMPLETE` 用 warning 而非 success，避免读成通过。 */
export function presentRunStatus(
  status: QualityRunStatus,
): QualityPresentation {
  if (status === "FAILED") {
    return { label: "FAILED（存在阻断违规）", tone: "error" };
  }
  if (status === "INCOMPLETE") {
    return { label: "INCOMPLETE（无结论）", tone: "warning" };
  }
  return { label: "PASSED（无阻断违规）", tone: "neutral" };
}

/** 单类聚合状态。只有 PASS 使用成功色；`NOT_APPLICABLE` 明确写"无结论"。 */
export function presentCategoryStatus(
  status: QualityCategoryStatus,
): QualityPresentation {
  switch (status) {
    case "PASS":
      return { label: "PASS（通过）", tone: "success" };
    case "FAIL":
      return { label: "FAIL（未通过）", tone: "error" };
    case "WARN":
      return { label: "WARN（仅提醒）", tone: "warning" };
    case "ERROR":
      return { label: "ERROR（规则执行失败）", tone: "error" };
    case "NOT_APPLICABLE":
      return { label: "NOT_APPLICABLE（不适用，无结论）", tone: "neutral" };
  }
}

/** 单条规则结论。`NOT_APPLICABLE` 与 `ERROR` 都不得显示成通过。 */
export function presentFindingStatus(
  status: QualityFindingStatus,
): QualityPresentation {
  switch (status) {
    case "PASS":
      return { label: "通过", tone: "success" };
    case "FAIL":
      return { label: "未通过", tone: "error" };
    case "NOT_APPLICABLE":
      return { label: "无结论", tone: "neutral" };
    case "ERROR":
      return { label: "执行失败（无结论）", tone: "warning" };
  }
}

export function presentSeverity(
  severity: QualitySeverity,
): QualityPresentation {
  return severity === "ERROR"
    ? { label: "ERROR（阻断）", tone: "error" }
    : { label: "WARN（提醒）", tone: "warning" };
}

/** `null` 不是 0 分：没有可判定结论时如实显示，不给出会被误读的数字。 */
export function formatQualityScore(score: number | null): string {
  return score === null ? "无可用评分（无结论）" : score.toFixed(2);
}

export function isRunIncomplete(run: QualityRun): boolean {
  return run.status === "INCOMPLETE";
}

/** 没有结论的类别（含派生 OVERALL），供页面汇总提示。 */
export function notApplicableCategories(run: QualityRun): QualityCategory[] {
  return run.categories
    .filter((item) => item.status === "NOT_APPLICABLE")
    .map((item) => item.category);
}

/**
 * 严重度筛选：契约的结论明细接口只支持 category/status，severity 只能在前端
 * 过滤。调用方必须同时告知"只作用于已加载条目"，避免把部分结果说成全量。
 */
export function filterFindingsBySeverity(
  findings: readonly QualityRunFinding[],
  severity: QualitySeverity | null,
): QualityRunFinding[] {
  if (severity === null) {
    return [...findings];
  }
  return findings.filter((finding) => finding.severity === severity);
}

/** 结论是否指向具体对象；对象级结论 `subject_ref` 为空字符串。 */
export function findingSubjectLabel(finding: QualityRunFinding): string {
  return finding.subject_ref === "" ? "对象级" : finding.subject_ref;
}

/** 筛选器的"全部"取值；避免把空字符串当成一个真实枚举值。 */
export const QUALITY_FILTER_ALL = "ALL";

/**
 * 类别筛选值 → 规则类别。
 *
 * `OVERALL` 是派生聚合，结论明细接口对它返回空列表，因此 UI 不提供该选项；
 * 传入非规则类别时回到"全部"，不把未知值发给服务端。
 */
export function readRuleCategoryFilter(
  value: string,
): QualityRuleCategory | null {
  if (value === QUALITY_FILTER_ALL) {
    return null;
  }
  return QUALITY_RULE_CATEGORY_VALUES.find((item) => item === value) ?? null;
}

export function readSeverityFilter(value: string): QualitySeverity | null {
  if (value === QUALITY_FILTER_ALL) {
    return null;
  }
  return QUALITY_SEVERITY_VALUES.find((item) => item === value) ?? null;
}
