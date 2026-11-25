import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { AnnotationRecord, LabelValue } from "../../../types/annotation";
import type {
  PreferencePairAnnotationValue,
  PreferencePairItemPayload,
} from "../../../types/llmAnnotation";
import LlmItemSection from "./LlmItemSection";

function label(labelKey: string, value: unknown): LabelValue {
  return {
    label_key: labelKey,
    value,
    source: "HUMAN",
    label_status: "CONFIRMED",
  };
}

function record(
  annotationId: string,
  labels: LabelValue[],
): AnnotationRecord {
  return {
    annotation_id: annotationId,
    annotation_lineage_id: "eeeeeeee-ffff-7aaa-8bbb-cccccccccccc",
    annotation_version: 1,
    project_id: "p",
    submission_id: "s",
    episode_id: "e",
    episode_version: 1,
    task_id: "t",
    schema_id: "sc",
    schema_version: 1,
    status: "SUBMITTED",
    time_range: null,
    labels,
    evidence_refs: [],
    record_sha256: "sha256:" + "c".repeat(64),
    supersedes_id: null,
    approved_by: null,
    approved_at: null,
    version: 1,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
  };
}

const PREF_PAYLOAD: PreferencePairItemPayload = {
  prompt: "q",
  chosen: "A 内容",
  rejected: "B 内容",
  criteria: [],
  annotator_rationale: null,
};

const TEXT_RECORD = record("11111111-1111-7111-8111-111111111111", [
  label("item_type", "TEXT"),
  label("item_payload", { text: "文本正文", source_ref: null, language: null, metadata: null }),
]);

const DIALOGUE_RECORD = record("22222222-2222-7222-8222-222222222222", [
  label("item_type", "DIALOGUE"),
  label("item_payload", { turns: [{ role: "user", content: "你好", ts: null }] }),
]);

const PREF_RECORD = record("33333333-3333-7333-8333-333333333333", [
  label("item_type", "PREFERENCE_PAIR"),
  label("item_payload", PREF_PAYLOAD),
]);

const TRACE_RECORD = record("44444444-4444-7444-8444-444444444444", [
  label("item_type", "AGENT_TRACE"),
  label("item_payload", { steps: [{ action: "step1" }], task_goal: null, success: null }),
]);

describe("LlmItemSection 分发", () => {
  it("四种类型分发到对应组件", () => {
    const html = renderToStaticMarkup(
      <LlmItemSection
        records={[
          TEXT_RECORD,
          DIALOGUE_RECORD,
          PREF_RECORD,
          TRACE_RECORD,
        ]}
      />,
    );
    expect(html).toContain("文本对象（TEXT）");
    expect(html).toContain("文本正文");
    expect(html).toContain("对话对象（DIALOGUE");
    expect(html).toContain("你好");
    expect(html).toContain("偏好对比（PREFERENCE_PAIR）");
    expect(html).toContain("A 内容");
    expect(html).toContain("智能体轨迹（AGENT_TRACE");
    expect(html).toContain("step1");
  });

  it("未知类型与缺标签记录回落既有表单（不渲染任何 LLM 块）", () => {
    const html = renderToStaticMarkup(
      <LlmItemSection
        records={[
          record("55555555-5555-7555-8555-555555555555", [
            label("item_type", "VIDEO"),
            label("item_payload", { text: "x" }),
          ]),
          record("66666666-6666-7666-8666-666666666666", [
            label("label_key_value", "普通标注"),
          ]),
        ]}
      />,
    );
    expect(html).toBe("");
  });

  it("混合记录只渲染可解析部分", () => {
    const html = renderToStaticMarkup(
      <LlmItemSection records={[TEXT_RECORD, PREF_RECORD]} />,
    );
    expect(html).toContain("文本对象（TEXT）");
    expect(html).toContain("偏好对比（PREFERENCE_PAIR）");
  });

  it("受控偏好值高亮并回传 onChange", () => {
    const value: Record<string, PreferencePairAnnotationValue | null> = {
      "33333333-3333-7333-8333-333333333333": {
        winner: "TIE",
        confidence: 0.5,
        rationale: "不相上下",
      },
    };
    const html = renderToStaticMarkup(
      <LlmItemSection records={[PREF_RECORD]} preferenceValue={value} />,
    );
    expect(html).toContain("0.5");
    expect(html).toContain("不相上下");
  });

  it("onChange 回调签名可用（类型契约冒烟）", () => {
    const onChange = vi.fn();
    expect(() =>
      renderToStaticMarkup(
        <LlmItemSection records={[PREF_RECORD]} onPreferenceChange={onChange} />,
      ),
    ).not.toThrow();
  });
});
