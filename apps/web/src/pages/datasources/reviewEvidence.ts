/** 审核证据表单 -> 契约对象的纯转换。
 *
 * 单独成模块，避免页面文件的 react-refresh 规则把非组件导出当成告警。
 */

import type { ReviewEvidence } from "../../types/datasource";

export interface ReviewFormValues {
  reason?: string;
  evidence_timestamp?: number | null;
  evidence_camera?: string | null;
  evidence_note?: string | null;
}

/**
 * 把表单里的可选证据字段收敛成契约对象；空值不提交。
 * 时间点 0 秒是合法值，不能用 falsy 判断丢掉。
 */
export function buildReviewEvidence(
  values: ReviewFormValues,
): ReviewEvidence | null {
  const evidence: ReviewEvidence = {};
  if (
    typeof values.evidence_timestamp === "number" &&
    Number.isFinite(values.evidence_timestamp)
  ) {
    evidence.timestamp_seconds = values.evidence_timestamp;
  }
  const camera = (values.evidence_camera ?? "").trim();
  if (camera) {
    evidence.camera = camera;
  }
  const note = (values.evidence_note ?? "").trim();
  if (note) {
    evidence.note = note;
  }
  return Object.keys(evidence).length > 0 ? evidence : null;
}
