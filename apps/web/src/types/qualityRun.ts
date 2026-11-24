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

/**
 * 证据时间区间（契约 `QualityEvidenceTimeRange`）。
 *
 * 起止是**十进制字符串**而不是 number：纳秒约 `1.8e18`，超出 `2^53`，
 * 浏览器 `JSON.parse` 会把数字静默改值——定位到错误时间点比不定位更糟。
 */
export interface QualityEvidenceTimeRange {
  start_time_ns: string;
  end_time_ns: string;
}

/**
 * 一条质量结论的证据定位（契约 `QualityEvidenceLocation`，V51-010 / FR-025）。
 *
 * 三个维度相互独立且都可空；整体为 `null` 表示"无法定位"，界面必须如实
 * 说明，不得渲染成"定位到 0 时刻"。
 */
export interface QualityEvidenceLocation {
  time_range: QualityEvidenceTimeRange | null;
  stream_id: string | null;
  field_path: string | null;
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
  /**
   * 证据定位（V51-010）：时间区间 / 媒体流 / 字段路径。
   * `null` 表示该结论无法定位——界面必须单独表达，不能当作"定位到 0 时刻"。
   */
  evidence_location: QualityEvidenceLocation | null;
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

// ---------------------------------------------------------------------------
// V31-033 / FR-026：批量重跑（只入队，无结论）
// ---------------------------------------------------------------------------

/**
 * 排队中的质量运行（契约 `QueuedQualityRun`）。
 *
 * **没有结论字段**：`status` / `score` / 六类聚合 / 起止时间在排队阶段
 * 不存在。这是独立 schema 的类型事实——前端不得把「入队成功」渲染成
 * 「检查通过」（ADR-0045 语义红线）。
 */
export interface QueuedQualityRun {
  run_id: string;
  project_id: string;
  subject_type: QualitySubjectType;
  subject_id: string;
  /** 入队时固化的规则声明内容 SHA（区别于执行时的适用规则集）。 */
  rule_set_version: string;
  /** 排队响应固定为 `QUEUED`；其余取值出现在后续查询入口。 */
  lifecycle: QueuedQualityRunLifecycle;
  max_attempts: number;
  created_at: string;
  created_by: string;
}

/** 契约 `QueuedQualityRun.lifecycle` 的封闭枚举；未知值解析时拒绝。 */
export const QUEUED_QUALITY_RUN_LIFECYCLE_VALUES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "RETRYING",
  "CANCELLED",
] as const;
export type QueuedQualityRunLifecycle =
  (typeof QUEUED_QUALITY_RUN_LIFECYCLE_VALUES)[number];

/** POST `/quality-runs/batch` 的入参；服务端只接受 EPISODE。 */
export interface QualityRunBatchCreateInput {
  subject_type: "EPISODE";
  /** 1..1000 个 Episode，不得重复；重复在服务端是 422。 */
  subject_ids: string[];
}

// ---------------------------------------------------------------------------
// V31-033 / FR-027：差异报告（按 rule_id + subject_ref 对齐）
// ---------------------------------------------------------------------------

/** 差异四分类：新增 / 已消除 / 无变化 / 结论变化。 */
export const QUALITY_DIFF_KIND_VALUES = [
  "NEW",
  "RESOLVED",
  "UNCHANGED",
  "CHANGED",
] as const;
export type QualityDiffKind = (typeof QUALITY_DIFF_KIND_VALUES)[number];

/** 差异条目中的单侧结论；`null` 表示该侧没有这条结论。 */
export const QUALITY_DIFF_STATUS_VALUES = [
  "PASS",
  "FAIL",
  "NOT_APPLICABLE",
  "ERROR",
] as const;
export type QualityDiffStatus = (typeof QUALITY_DIFF_STATUS_VALUES)[number];

/**
 * 单条差异（契约 `QualityFindingDiff`）。
 *
 * `subject_ref` 区分同一规则作用于多个对象；对象级结论为空字符串，
 * 展示层用 `—` 表示，不伪造对象名。
 */
export interface QualityFindingDiff {
  rule_id: string;
  subject_ref: string;
  kind: QualityDiffKind;
  baseline_status: QualityDiffStatus | null;
  candidate_status: QualityDiffStatus | null;
}

/** 差异报告（契约 `QualityRunDiff`）；四个计数必须与 findings 一致。 */
export interface QualityRunDiffReport {
  baseline_run_id: string;
  candidate_run_id: string;
  findings: QualityFindingDiff[];
  new_count: number;
  resolved_count: number;
  unchanged_count: number;
  changed_count: number;
  /** 只把 `NEW` 算作回归；`CHANGED` 可能是修好也可能是变坏。 */
  has_regression: boolean;
}
