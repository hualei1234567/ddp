/**
 * 质量管理模块类型（N4 标注质量检查 + 黄金集 / IAA 的写请求入参）。
 *
 * 依据契约：
 * - `packages/contracts/fragments/schemas.stage9.yaml`（`QualityFinding` / `QualityCheckRun`）
 * - `packages/contracts/fragments/paths.stage8.yaml#/submissionQualityChecks`
 * - `packages/contracts/fragments/schemas.stage3.yaml`（`GoldSetCreate` / `IAAReportCreate`）
 *
 * 只声明契约里出现过的字段；后端实现存在但契约未声明的字段不在这里猜测。
 * 黄金集与抽检的读模型仍留在 `types/annotation.ts`，本文件只补质量检查与写请求。
 */

/** 规则严重级别：ERROR 计入失败判定，WARN 只提醒。 */
export const QUALITY_RULE_SEVERITY_VALUES = ["ERROR", "WARN"] as const;
export type QualityRuleSeverity = (typeof QUALITY_RULE_SEVERITY_VALUES)[number];

/** 一次检查的整体结论。同步执行，因此没有 RUNNING/ERROR 之类的执行态。 */
export const QUALITY_CHECK_STATUS_VALUES = ["PASSED", "FAILED"] as const;
export type QualityCheckStatus = (typeof QUALITY_CHECK_STATUS_VALUES)[number];

export interface QualityFinding {
  annotation_id: string;
  rule_id: string;
  rule_version: number;
  severity: QualityRuleSeverity;
  /**
   * 三态判定：
   * - `true`：规则判定合规；
   * - `false`：规则判定违规；
   * - `null`：**该规则对这条记录没有结论**（缺 Episode 边界、任务未配阈值、
   *   记录没有该标签等）。
   *
   * 契约（`schemas.stage9.yaml#/QualityFinding`）明确要求「不得把 `null` 当成通过」，
   * 因此类型上保留 `null`，渲染层必须单独表达，不能落到 `false` 或 `true` 分支。
   */
  passed: boolean | null;
  message: string;
}

export interface QualityCheckRun {
  run_id: string;
  project_id: string;
  submission_id: string;
  /** 规则集内容摘要（`sha256:` 前缀），回答「当时用的是哪一版规则」。 */
  rule_set_version: string;
  status: QualityCheckStatus;
  error_count: number;
  warn_count: number;
  started_at: string;
  finished_at: string;
  findings: QualityFinding[];
  created_at: string | null;
  created_by: string | null;
  /**
   * 契约把 `updated_at` / `updated_by` 列为必填，但后端 `QualityCheckOut`
   * 未返回这两个字段（只追加记录没有更新语义）。前端按可选处理，不因缺字段
   * 拒绝渲染整页；该差异已登记为契约缺口，不在前端「补」后端行为。
   */
  updated_at?: string | null;
  updated_by?: string | null;
}

/**
 * 创建黄金集的入参（契约 `GoldSetCreate`）。
 *
 * `items` 在契约里只声明为「对象数组、至少 1 项、字段自由」，具体的键
 * （`episode_id` / `expected_labels` 等）由领域层解释、契约未声明，因此前端
 * 不做逐字段建模，只做「JSON 对象数组」这一层校验。
 */
export interface GoldSetCreateInput {
  code: string;
  name?: string | null;
  schema_id: string;
  schema_version?: number | null;
  schema_sha256?: string | null;
  items: Record<string, unknown>[];
  exposure_policy: string;
  max_exposure_per_annotator: number;
  rotation_policy: string;
  usage_policy: Record<string, unknown>;
}

/** 创建 IAA 计算（契约 `IAAReportCreate`）。 */
export interface IAAReportCreateInput {
  task_id: string;
  metric_definitions_version: string;
  iou_threshold: number;
}
