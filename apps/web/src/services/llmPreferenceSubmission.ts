/**
 * 偏好对比标注值的提交 payload 构造（MEM-DEC-002 JSON 约定值）。
 *
 * ## 背景与边界（如实说明后端缺口）
 *
 * `PreferencePairPanel` 的 `{winner, confidence, rationale}` 只是页面草稿态。
 * MEM-DEC-002 规定偏好结论以**现有 JSON 标签类型的约定值**落库（不新增标签
 * 类型），但服务端当前**没有**为该约定值提供 schema preset、提交校验或记录
 * 更新端点——已 grep 确认 `backend/app` 里没有 `PREFERENCE_PAIR` 的标签值校验
 * （只有 PRM Step-DPO 导出侧的 `PreferenceRecord` 形态）。
 *
 * 因此本模块只做前端能做且可测的部分：把草稿规范成可提交的 label 值 / 记录
 * payload。真正的写入通道（label 键与后端校验的对齐）是待补的后端工作，见
 * `PREFERENCE_LABEL_KEY` 的说明与 PR 报告；本模块不伪造接口、不假装成功。
 *
 * ## 规范口径
 *
 * - `winner` ∈ `A|B|TIE`（与 `PREFERENCE_WINNER_VALUES` 同源）；
 * - `confidence` ∈ [0, 1] 或 `null`；超出范围 / NaN 属于输入错误，fail-closed：
 *   该条不生成写入，交给调用方展示具体原因；
 * - `rationale` 去首尾空白，空串归一为 `null`；长度上限 2000（与后端
 *   `prm_dpo` 的 `optional_text(max_length=2000)` 对齐）；
 * - 未选择 winner（`null`）不算有效结论，不产生写入。
 */

import type {
  AnnotationRecord,
  AnnotationWorkRecordInput,
  LabelValue,
} from "../types/annotation";
import {
  isPreferenceWinner,
  type PreferencePairAnnotationValue,
} from "../types/llmAnnotation";

/**
 * 偏好结论的 JSON 标签键（MEM-DEC-002 约定值的落库位置）。
 *
 * 后端目前**尚未**提供该 label 的 schema preset 与提交校验（见模块 docstring）；
 * 该常量是与后端对齐的唯一定义点，backend 落地后只需在此确认键名，不必改页面。
 */
export const PREFERENCE_LABEL_KEY = "preference_pair";

/** rationale 长度上限：与后端 `optional_text(..., max_length=2000)` 对齐。 */
export const PREFERENCE_RATIONALE_MAX_LENGTH = 2000;

/** 服务端尚未提供偏好值写入通道时的稳定提示（前端唯一文案来源）。 */
export const PREFERENCE_WRITE_UNSUPPORTED_MESSAGE =
  "服务端尚未提供偏好值写入通道（后端偏好校验待补）；偏好草稿已保留，本次未提交任何内容。";

export interface PreferenceLabelValue {
  winner: "A" | "B" | "TIE";
  confidence: number | null;
  rationale: string | null;
}

export type PreferenceLabelIssueCode =
  | "invalid_winner"
  | "confidence_out_of_range"
  | "rationale_too_long";

export interface PreferenceLabelIssue {
  annotation_id: string;
  code: PreferenceLabelIssueCode;
  message: string;
}

export interface PreferenceLabelWrite {
  annotation_id: string;
  label_key: string;
  value: PreferenceLabelValue;
}

export interface PreferenceSubmissionPayload {
  writes: PreferenceLabelWrite[];
  issues: PreferenceLabelIssue[];
}

/** 服务端缺写入通道：调用方据此给"未提交"的明确失败，而不是假装成功。 */
export class PreferenceWriteUnsupportedError extends Error {
  constructor(message: string = PREFERENCE_WRITE_UNSUPPORTED_MESSAGE) {
    super(message);
    this.name = "PreferenceWriteUnsupportedError";
  }
}

function normalizeConfidence(
  annotationId: string,
  raw: number | null,
): { confidence: number | null; issue: PreferenceLabelIssue | null } {
  if (raw === null) {
    return { confidence: null, issue: null };
  }
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0 || raw > 1) {
    return {
      confidence: null,
      issue: {
        annotation_id: annotationId,
        code: "confidence_out_of_range",
        message: "置信度必须在 0~1 之间（或留空）。",
      },
    };
  }
  return { confidence: raw, issue: null };
}

function normalizeRationale(
  annotationId: string,
  raw: string | null,
): { rationale: string | null; issue: PreferenceLabelIssue | null } {
  if (raw === null) {
    return { rationale: null, issue: null };
  }
  const trimmed = raw.trim();
  if (trimmed.length > PREFERENCE_RATIONALE_MAX_LENGTH) {
    return {
      rationale: null,
      issue: {
        annotation_id: annotationId,
        code: "rationale_too_long",
        message: `选择理由不得超过 ${PREFERENCE_RATIONALE_MAX_LENGTH} 个字符。`,
      },
    };
  }
  return { rationale: trimmed === "" ? null : trimmed, issue: null };
}

/** 单条草稿 → 约定值；返回 null 表示"未选 winner，不算有效结论"。 */
export function buildPreferenceLabelValue(
  annotationId: string,
  value: PreferencePairAnnotationValue | null,
): { value: PreferenceLabelValue | null; issues: PreferenceLabelIssue[] } {
  if (value === null) {
    return { value: null, issues: [] };
  }
  if (!isPreferenceWinner(value.winner)) {
    return {
      value: null,
      issues: [
        {
          annotation_id: annotationId,
          code: "invalid_winner",
          message: `winner 必须是 A/B/TIE，收到 ${String(value.winner)}。`,
        },
      ],
    };
  }
  const confidence = normalizeConfidence(annotationId, value.confidence);
  const rationale = normalizeRationale(annotationId, value.rationale);
  const issues = [confidence.issue, rationale.issue].filter(
    (issue): issue is PreferenceLabelIssue => issue !== null,
  );
  if (issues.length > 0) {
    // fail-closed：非法值不生成写入，避免把超出范围的数据提交给服务端。
    return { value: null, issues };
  }
  return {
    value: {
      winner: value.winner,
      confidence: confidence.confidence,
      rationale: rationale.rationale,
    },
    issues: [],
  };
}

/** 把页面草稿（按 annotation_id 分键）批量规范成提交 payload（按 id 排序）。 */
export function buildPreferenceSubmissionPayload(
  values: Record<string, PreferencePairAnnotationValue | null | undefined>,
): PreferenceSubmissionPayload {
  const writes: PreferenceLabelWrite[] = [];
  const issues: PreferenceLabelIssue[] = [];
  for (const annotationId of Object.keys(values).sort()) {
    const result = buildPreferenceLabelValue(
      annotationId,
      values[annotationId] ?? null,
    );
    issues.push(...result.issues);
    if (result.value !== null) {
      writes.push({
        annotation_id: annotationId,
        label_key: PREFERENCE_LABEL_KEY,
        value: result.value,
      });
    }
  }
  return { writes, issues };
}

/**
 * 把偏好 label 值合并进记录 payload（提交/更新记录时使用）。
 *
 * 只替换/追加 `preference_pair` 这一个 JSON 标签，其余标签与记录字段原样
 * 保留——偏好接线不得覆盖既有标注结论。
 */
export function mergePreferenceLabelsIntoRecords(
  records: AnnotationRecord[],
  writes: PreferenceLabelWrite[],
): AnnotationWorkRecordInput[] {
  const byAnnotation = new Map(
    writes.map((write) => [write.annotation_id, write.value]),
  );
  return records.map((record) => {
    const merged = byAnnotation.get(record.annotation_id);
    const labels: LabelValue[] = record.labels.map((label) => ({ ...label }));
    if (merged !== undefined) {
      const next: LabelValue = {
        label_key: PREFERENCE_LABEL_KEY,
        value: merged,
        source: "HUMAN",
        label_status: "CONFIRMED",
      };
      const index = labels.findIndex(
        (label) => label.label_key === PREFERENCE_LABEL_KEY,
      );
      if (index >= 0) {
        labels[index] = next;
      } else {
        labels.push(next);
      }
    }
    return {
      episode_id: record.episode_id,
      episode_version: record.episode_version,
      annotation_lineage_id: record.annotation_lineage_id,
      annotation_version: record.annotation_version,
      time_range: record.time_range,
      labels,
      evidence_refs: record.evidence_refs,
    };
  });
}

/**
 * 当前默认写入器：服务端没有偏好值写入通道，因此**显式失败**。
 *
 * 这是接线现状的真实表达（不是占位 mock）：后端补上 schema preset + 提交校验
 * + 记录更新端点后，把这个函数替换成真实 API 调用即可，页面与测试不需改动。
 * 禁止在这里静默 resolve —— 那会让用户以为偏好值已保存。
 */
export async function writePreferenceLabelsUnsupported(): Promise<never> {
  throw new PreferenceWriteUnsupportedError();
}

/** 校验问题 → 人可读一段话（页面反馈描述用）。 */
export function describePreferenceIssues(issues: PreferenceLabelIssue[]): string {
  return issues
    .map((issue) => `标注对象 ${issue.annotation_id}：${issue.message}`)
    .join("；");
}
