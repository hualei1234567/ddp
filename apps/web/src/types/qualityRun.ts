/**
 * N4-004 统一质量引擎的类型（六类分层）。
 *
 * 依据契约：
 * - `packages/contracts/fragments/schemas.quality.yaml`
 * - `packages/contracts/fragments/paths.quality.yaml`
 *
 * 与 `types/quality.ts`（标注提交的三态质量检查）刻意分开：那是标注域的
 * `QualityCheckRun`，这里是跨对象的统一质量运行，字段与结论口径不同。
 * 关键语义（由契约固定，前端不得改写）：
 * - `NOT_APPLICABLE` 表示**没有结论**，不是通过；
 * - `INCOMPLETE` 表示本次运行所有规则都没有结论，既非通过也非失败；
 * - `score === null` 表示没有可判定结论，不是 0 分。
 */

/** 六类分层；`OVERALL` 是服务端派生的聚合类别。 */
export const QUALITY_CATEGORY_VALUES = [
  "INTEGRITY",
  "TEMPORAL",
  "VISUAL",
  "METADATA",
  "ANNOTATION",
  "OVERALL",
] as const;
export type QualityCategory = (typeof QUALITY_CATEGORY_VALUES)[number];

/** 规则源实际产出的类别；`OVERALL` 不能用于筛选结论明细。 */
export const QUALITY_RULE_CATEGORY_VALUES = [
  "INTEGRITY",
  "TEMPORAL",
  "VISUAL",
  "METADATA",
  "ANNOTATION",
] as const;
export type QualityRuleCategory = (typeof QUALITY_RULE_CATEGORY_VALUES)[number];

export const QUALITY_CATEGORY_STATUS_VALUES = [
  "PASS",
  "FAIL",
  "WARN",
  "ERROR",
  "NOT_APPLICABLE",
] as const;
export type QualityCategoryStatus =
  (typeof QUALITY_CATEGORY_STATUS_VALUES)[number];

export const QUALITY_FINDING_STATUS_VALUES = [
  "PASS",
  "FAIL",
  "NOT_APPLICABLE",
  "ERROR",
] as const;
export type QualityFindingStatus =
  (typeof QUALITY_FINDING_STATUS_VALUES)[number];

export const QUALITY_SEVERITY_VALUES = ["ERROR", "WARN"] as const;
export type QualitySeverity = (typeof QUALITY_SEVERITY_VALUES)[number];

export const QUALITY_RUN_STATUS_VALUES = [
  "PASSED",
  "FAILED",
  "INCOMPLETE",
] as const;
export type QualityRunStatus = (typeof QUALITY_RUN_STATUS_VALUES)[number];

export const QUALITY_SUBJECT_TYPE_VALUES = [
  "EPISODE",
  "DATASOURCE_IMPORT",
  "DATASET_VERSION",
] as const;
export type QualitySubjectType =
  (typeof QUALITY_SUBJECT_TYPE_VALUES)[number];

/** 六类中的一类聚合结论；六类始终齐全（含 `OVERALL`）。 */
export interface QualityCategorySummary {
  category: QualityCategory;
  status: QualityCategoryStatus;
  finding_count: number;
  pass_count: number;
  fail_count: number;
  not_applicable_count: number;
  /** 该类别中规则执行失败（finding.status=ERROR）的条数。 */
  error_count: number;
  /** 可判定结论的平均分；没有可判定结论时为 null（不是 0 分）。 */
  score: number | null;
}

export interface QualityRun {
  run_id: string;
  project_id: string;
  subject_type: QualitySubjectType;
  subject_id: string;
  /** 适用规则声明的内容 SHA，运行创建时固化。 */
  rule_set_version: string;
  status: QualityRunStatus;
  /** 全部可判定结论的平均分；`INCOMPLETE` 时为 null。 */
  score: number | null;
  finding_count: number;
  /** ERROR 级未通过规则数（阻断）。 */
  error_count: number;
  /** WARN 级未通过规则数（不阻断）。 */
  warn_count: number;
  categories: QualityCategorySummary[];
  started_at: string;
  finished_at: string;
  created_at: string;
  created_by: string;
}

export interface QualityRunFinding {
  finding_id: string;
  run_id: string;
  category: QualityCategory;
  rule_id: string;
  /** 该条规则当时的版本；与运行级规则集版本一起保证历史可解释。 */
  rule_version: string;
  severity: QualitySeverity;
  status: QualityFindingStatus;
  /** 同一规则作用于多个对象时的对象标识；对象级结论为空字符串。 */
  subject_ref: string;
  message: string;
  /** 结构化排障信息；契约未约束内部字段，前端原样展示。 */
  evidence: Record<string, unknown> | null;
  /** PASS=1.0 / FAIL=0.0；NOT_APPLICABLE 与 ERROR 为 null。 */
  score: number | null;
  created_at: string;
}

/** POST `/quality-runs` 的入参（本批次只接受单个对象）。 */
export interface QualityRunCreateInput {
  subject_type: "EPISODE" | "DATASOURCE_IMPORT";
  subject_id: string;
  /** 可选类别筛选；`OVERALL` 是派生类别，不能用于筛选规则。 */
  categories?: QualityCategory[] | null;
}
