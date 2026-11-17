/**
 * 质量管理的展示层映射（纯函数，可单测）。
 *
 * 这里只解决"服务端值怎么显示"，不做任何权限或状态机判断。
 * 三态 `passed` 的显示规则由契约固定：
 * `packages/contracts/fragments/schemas.stage9.yaml#/QualityFinding` 明确写
 * "`null` 表示该规则对这条记录没有结论，**不得把 `null` 当成通过**"。
 */

import type {
  QualityCheckStatus,
  QualityFinding,
  QualityRuleSeverity,
} from "../types/quality";

export type QualityTone = "success" | "error" | "neutral" | "warning";

export interface QualityPresentation {
  label: string;
  tone: QualityTone;
}

/**
 * 三态判定 → 展示。
 *
 * - `true`  → 通过
 * - `false` → 未通过
 * - `null`  → 无结论（该规则对该记录没有判定依据）
 *
 * 任何"合并成两态"的实现都会把"没判"说成"判了通过"，所以这里显式三分支，
 * 且 `null` 分支不返回 `success`。
 */
export function presentFindingResult(passed: boolean | null): QualityPresentation {
  if (passed === true) {
    return { label: "通过", tone: "success" };
  }
  if (passed === false) {
    return { label: "未通过", tone: "error" };
  }
  return { label: "无结论", tone: "neutral" };
}

/** 无结论的补充说明，供 Tooltip/说明文案复用。 */
export const INCONCLUSIVE_FINDING_HINT =
  "无结论表示该规则对这条记录没有判定依据（缺 Episode 边界、任务未配阈值、记录没有对应标签等），不代表合规。";

export interface QualityFindingSummary {
  passedCount: number;
  failedCount: number;
  inconclusiveCount: number;
  total: number;
}

/**
 * 统计三种结论。
 *
 * 与后端 `summarize_findings` 的差异是刻意的：后端只统计违规（用于是否 FAILED），
 * 前端还要把"无结论"单独计数，否则页面无法解释"PASSED 但什么都没判"。
 */
export function summarizeFindings(
  findings: readonly QualityFinding[],
): QualityFindingSummary {
  let passedCount = 0;
  let failedCount = 0;
  let inconclusiveCount = 0;
  for (const finding of findings) {
    if (finding.passed === true) {
      passedCount += 1;
    } else if (finding.passed === false) {
      failedCount += 1;
    } else {
      inconclusiveCount += 1;
    }
  }
  return {
    passedCount,
    failedCount,
    inconclusiveCount,
    total: findings.length,
  };
}

/**
 * 本次检查是否"没有任何规则给出结论"。
 *
 * 后端整体结论只看 ERROR 级违规数（`resolve_status`：有 ERROR 即 FAILED），
 * 因此全为 `null` 的检查同样会得到 PASSED。页面必须在这种情形下显式提示
 * "PASSED 只表示没有 ERROR 级违规"，而不是让用户以为全部规则都通过了。
 */
export function isWholeRunInconclusive(
  summary: QualityFindingSummary,
): boolean {
  return (
    summary.total > 0 &&
    summary.passedCount === 0 &&
    summary.failedCount === 0
  );
}

/** 单次检查的结论文案：用服务端枚举语义，不额外承诺"合格"。 */
export function presentCheckStatus(
  status: QualityCheckStatus,
): QualityPresentation {
  if (status === "FAILED") {
    return { label: "存在 ERROR 级违规", tone: "error" };
  }
  return { label: "无 ERROR 级违规", tone: "neutral" };
}

export function presentRuleSeverity(
  severity: QualityRuleSeverity,
): QualityPresentation {
  if (severity === "ERROR") {
    return { label: "ERROR（阻断）", tone: "error" };
  }
  return { label: "WARN（提醒）", tone: "warning" };
}

export interface QualityActionOption {
  action: string;
  label: string;
  /** 破坏性动作，按钮标红以降低误点概率（不做可用性判断）。 */
  danger: boolean;
}

/**
 * 黄金集动作集合。
 *
 * 依据：契约 `paths.stage3.yaml#/goldSetActions` 的摘要声明的动作名
 * （submit-review / return-draft / activate / retire / revoke）。
 * `GoldSetOut` **没有** `allowed_actions` 字段，契约也没有声明黄金集的状态迁移表，
 * 所以前端不复制后端状态机，一律列出这几个动作，由服务端裁决是否允许；
 * 非法迁移由服务端返回 409/422，页面如实展示错误，而不是提前隐藏按钮。
 */
export const GOLD_SET_ACTIONS: readonly QualityActionOption[] = [
  { action: "submit-review", label: "提交审核", danger: false },
  { action: "return-draft", label: "退回草稿", danger: true },
  { action: "activate", label: "启用", danger: false },
  { action: "retire", label: "退役", danger: true },
  { action: "revoke", label: "撤销", danger: true },
];

/** 未知动作原样展示，不猜语义（服务端可能与前端版本不一致）。 */
export function presentGoldSetAction(action: string): string {
  const matched = GOLD_SET_ACTIONS.find((item) => item.action === action);
  return matched?.label ?? action;
}
