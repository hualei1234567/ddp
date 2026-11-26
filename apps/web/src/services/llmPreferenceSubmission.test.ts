import { describe, expect, it } from "vitest";

import type { PreferencePairAnnotationValue } from "../types/llmAnnotation";
import { annotationSubmissionFixture } from "./__fixtures__/annotationFixtures";
import {
  buildPreferenceLabelValue,
  buildPreferenceSubmissionPayload,
  describePreferenceIssues,
  mergePreferenceLabelsIntoRecords,
  PREFERENCE_LABEL_KEY,
  PreferenceWriteUnsupportedError,
  writePreferenceLabelsUnsupported,
} from "./llmPreferenceSubmission";

const ANNOTATION_ID = annotationSubmissionFixture.records[0]!.annotation_id;

const WIN: PreferencePairAnnotationValue = {
  winner: "A",
  confidence: 0.9,
  rationale: "A 覆盖了全部要点",
};

describe("偏好值 payload 构造（MEM-DEC-002 约定值）", () => {
  it("按约定值形态构造 label 值并附带标签键", () => {
    const { writes, issues } = buildPreferenceSubmissionPayload({
      [ANNOTATION_ID]: WIN,
    });
    expect(issues).toEqual([]);
    expect(writes).toEqual([
      {
        annotation_id: ANNOTATION_ID,
        label_key: "preference_pair",
        value: {
          winner: "A",
          confidence: 0.9,
          rationale: "A 覆盖了全部要点",
        },
      },
    ]);
    expect(PREFERENCE_LABEL_KEY).toBe("preference_pair");
  });

  it("TIE 是有效结论：winner=TIE 同样生成写入", () => {
    const value = buildPreferenceLabelValue(ANNOTATION_ID, {
      winner: "TIE",
      confidence: null,
      rationale: null,
    });
    expect(value.issues).toEqual([]);
    expect(value.value).toEqual({ winner: "TIE", confidence: null, rationale: null });
  });

  it("未选 winner（null）不算有效结论，不产生写入", () => {
    const payload = buildPreferenceSubmissionPayload({
      [ANNOTATION_ID]: null,
      "another-id": { winner: "B", confidence: null, rationale: "  " },
    });
    expect(payload.issues).toEqual([]);
    // 只有选了 B 的那条进入写入；rationale 空白归一为 null。
    expect(payload.writes).toEqual([
      {
        annotation_id: "another-id",
        label_key: PREFERENCE_LABEL_KEY,
        value: { winner: "B", confidence: null, rationale: null },
      },
    ]);
  });

  it("置信度越界 fail-closed：报具体原因且不生成写入", () => {
    const payload = buildPreferenceSubmissionPayload({
      [ANNOTATION_ID]: { winner: "B", confidence: 1.5, rationale: null },
    });
    expect(payload.writes).toEqual([]);
    expect(payload.issues).toEqual([
      {
        annotation_id: ANNOTATION_ID,
        code: "confidence_out_of_range",
        message: "置信度必须在 0~1 之间（或留空）。",
      },
    ]);
    expect(describePreferenceIssues(payload.issues)).toContain(ANNOTATION_ID);
  });

  it("rationale 超长 fail-closed（上限与后端 2000 对齐）", () => {
    const payload = buildPreferenceSubmissionPayload({
      [ANNOTATION_ID]: { winner: "A", confidence: null, rationale: "x".repeat(2001) },
    });
    expect(payload.writes).toEqual([]);
    expect(payload.issues[0]?.code).toBe("rationale_too_long");
  });
});

describe("偏好 label 值合并进记录 payload", () => {
  it("只新增 preference_pair 标签，既有标签与记录字段保持不变", () => {
    const { writes } = buildPreferenceSubmissionPayload({ [ANNOTATION_ID]: WIN });
    const [record] = mergePreferenceLabelsIntoRecords(
      annotationSubmissionFixture.records,
      writes,
    );
    expect(record?.labels).toContainEqual({
      label_key: PREFERENCE_LABEL_KEY,
      value: { winner: "A", confidence: 0.9, rationale: "A 覆盖了全部要点" },
      source: "HUMAN",
      label_status: "CONFIRMED",
    });
    // 既有标签保留。
    expect(record?.labels.some((label) => label.label_key === "action")).toBe(true);
    expect(record?.episode_id).toBe(annotationSubmissionFixture.records[0]!.episode_id);
  });

  it("已存在 preference_pair 标签时就地替换而不是重复追加", () => {
    const base = annotationSubmissionFixture.records[0]!;
    const withExisting = {
      ...base,
      labels: [
        ...base.labels,
        {
          label_key: PREFERENCE_LABEL_KEY,
          value: { winner: "TIE" },
          source: "HUMAN" as const,
          label_status: "DRAFT" as const,
        },
      ],
    };
    const { writes } = buildPreferenceSubmissionPayload({ [ANNOTATION_ID]: WIN });
    const [record] = mergePreferenceLabelsIntoRecords([withExisting], writes);
    const preferenceLabels = record?.labels.filter(
      (label) => label.label_key === PREFERENCE_LABEL_KEY,
    );
    expect(preferenceLabels).toHaveLength(1);
    expect(preferenceLabels?.[0]?.value).toEqual({
      winner: "A",
      confidence: 0.9,
      rationale: "A 覆盖了全部要点",
    });
  });
});

describe("服务端写入通道缺口", () => {
  it("默认写入器显式失败（不假装成功）", async () => {
    await expect(writePreferenceLabelsUnsupported()).rejects.toBeInstanceOf(
      PreferenceWriteUnsupportedError,
    );
  });
});
