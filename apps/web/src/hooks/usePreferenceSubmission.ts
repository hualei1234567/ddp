/**
 * 偏好值提交接线（V52-033 遗留收口）。
 *
 * 把 `PreferencePairPanel` 的草稿态真正接到页面的"提交"动作上：
 *
 * 1. 提交前先按 MEM-DEC-002 规范成 label 值并合并进记录 payload
 *    （`buildPreferenceSubmissionPayload` / `mergePreferenceLabelsIntoRecords`）；
 * 2. 校验失败 → **不提交**、草稿原样保留、返回具体字段原因；
 * 3. 写入失败（含服务端尚无写入通道）→ **草稿原样保留**并返回错误反馈，
 *    绝不静默丢弃用户输入；
 * 4. 无草稿时返回 `empty`，页面走既有提交链路（零回归）。
 *
 * 后端缺口：当前没有偏好 label 的 schema preset / 提交校验 / 记录更新端点，
 * 因此默认写入器 `writePreferenceLabelsUnsupported` 会显式失败；页面据此给出
 * 明确的"未提交"错误。接入真实端点后只需替换写入器。
 */

import { useCallback, useMemo, useState } from "react";

import type { AnnotationRecord, AnnotationWorkRecordInput } from "../types/annotation";
import type { PreferencePairAnnotationValue } from "../types/llmAnnotation";
import {
  buildPreferenceSubmissionPayload,
  describePreferenceIssues,
  mergePreferenceLabelsIntoRecords,
  PreferenceWriteUnsupportedError,
} from "../services/llmPreferenceSubmission";
import {
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../services/annotationPresentation";

/** 写入器：把合并后的记录 payload 交给服务端（真实端点接入点）。 */
export type PreferenceRecordsWriter = (
  records: AnnotationWorkRecordInput[],
) => Promise<void>;

export type PreferenceSubmitOutcomeKind =
  | "submitted"
  | "blocked"
  | "invalid"
  | "failed"
  | "empty";

export interface PreferenceSubmitOutcome {
  kind: PreferenceSubmitOutcomeKind;
  feedback: AnnotationFeedback | null;
}

export interface PreferenceSubmissionState {
  /** 是否有任何已选 winner 的草稿（决定页面是否走偏好提交链路）。 */
  hasDrafts: boolean;
  busy: boolean;
  submit: () => Promise<PreferenceSubmitOutcome>;
}

export function usePreferenceSubmission({
  values,
  records,
  writeLabels,
}: {
  values: Record<string, PreferencePairAnnotationValue | null>;
  records: AnnotationRecord[];
  writeLabels: PreferenceRecordsWriter;
}): PreferenceSubmissionState {
  const [busy, setBusy] = useState(false);

  // 只关心"已选 winner"的草稿：winner 为空不算有效结论（与面板语义一致）。
  const hasDrafts = useMemo(
    () => Object.values(values).some((value) => value !== null),
    [values],
  );

  const submit = useCallback(async (): Promise<PreferenceSubmitOutcome> => {
    const payload = buildPreferenceSubmissionPayload(values);
    if (payload.issues.length > 0) {
      return {
        kind: "invalid",
        feedback: {
          kind: "error",
          title: "偏好值校验未通过",
          description: `${describePreferenceIssues(payload.issues)} 草稿已保留，修正后再提交。`,
        },
      };
    }
    if (payload.writes.length === 0) {
      return { kind: "empty", feedback: null };
    }
    setBusy(true);
    try {
      // 合并后的记录 payload 是真实写入器的入参；未接端点前写入器显式失败。
      await writeLabels(mergePreferenceLabelsIntoRecords(records, payload.writes));
      return {
        kind: "submitted",
        feedback: {
          kind: "success",
          title: "偏好值已提交",
          description: `已写入 ${payload.writes.length} 条偏好结论。`,
        },
      };
    } catch (caught) {
      if (caught instanceof PreferenceWriteUnsupportedError) {
        return {
          kind: "blocked",
          feedback: {
            kind: "error",
            title: "偏好值未提交",
            description: caught.message,
          },
        };
      }
      // 网络/服务端失败：草稿仍在页面状态里（本 hook 不清空任何输入）。
      return { kind: "failed", feedback: describeAnnotationActionError(caught) };
    } finally {
      setBusy(false);
    }
  }, [values, records, writeLabels]);

  return { hasDrafts, busy, submit };
}
