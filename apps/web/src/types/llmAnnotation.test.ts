import { describe, expect, it } from "vitest";

import type { AnnotationRecord, LabelValue } from "./annotation";
import {
  AGENT_STEP_LABEL_VALUES,
  isAgentStepLabel,
  isLlmItemType,
  isPreferenceWinner,
  LLM_ITEM_TYPE_VALUES,
  PREFERENCE_WINNER_VALUES,
  resolveLlmItem,
} from "./llmAnnotation";

function recordWith(labels: LabelValue[]): AnnotationRecord {
  // 仅取展示解析所需字段的最小记录形态；其余字段由既有 fixture 契约保证。
  return {
    annotation_id: "dddddddd-eeee-7fff-8aaa-bbbbbbbbbbbb",
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

function label(labelKey: string, value: unknown): LabelValue {
  return { label_key: labelKey, value, source: "HUMAN", label_status: "CONFIRMED" };
}

describe("LLM 标注类型与解析", () => {
  it("值域与契约/决策对齐", () => {
    expect([...LLM_ITEM_TYPE_VALUES]).toEqual([
      "TEXT",
      "DIALOGUE",
      "PREFERENCE_PAIR",
      "AGENT_TRACE",
    ]);
    expect([...PREFERENCE_WINNER_VALUES]).toEqual(["A", "B", "TIE"]);
    expect([...AGENT_STEP_LABEL_VALUES]).toEqual([
      "CORRECT",
      "REDUNDANT",
      "HALLUCINATED_TOOL",
      "FATAL",
    ]);
    expect(isLlmItemType("TEXT")).toBe(true);
    expect(isLlmItemType("VIDEO")).toBe(false);
    expect(isPreferenceWinner("TIE")).toBe(true);
    expect(isPreferenceWinner("A>B")).toBe(false);
    expect(isAgentStepLabel("FATAL")).toBe(true);
    expect(isAgentStepLabel("correct")).toBe(false);
  });

  it("TEXT：解析完整 payload", () => {
    const item = resolveLlmItem(
      recordWith([
        label("item_type", "TEXT"),
        label("item_payload", {
          text: "正文",
          source_ref: "doc-1",
          language: "zh",
          metadata: { page: 3 },
        }),
      ]),
    );
    expect(item).not.toBeNull();
    expect(item?.itemType).toBe("TEXT");
    if (item?.itemType === "TEXT") {
      expect(item.payload.text).toBe("正文");
      expect(item.payload.language).toBe("zh");
      expect(item.payload.metadata).toEqual({ page: 3 });
    }
  });

  it("DIALOGUE：turns 缺 role/content 或为空时拒绝", () => {
    const base = recordWith([label("item_type", "DIALOGUE")]);
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "DIALOGUE"),
          label("item_payload", { turns: [{ role: "user" }] }),
        ]),
      ),
    ).toBeNull();
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "DIALOGUE"),
          label("item_payload", { turns: [] }),
        ]),
      ),
    ).toBeNull();
    expect(base.labels.length).toBe(1);
  });

  it("PREFERENCE_PAIR：缺 chosen/rejected 拒绝", () => {
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "PREFERENCE_PAIR"),
          label("item_payload", { prompt: "q", chosen: "a" }),
        ]),
      ),
    ).toBeNull();
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "PREFERENCE_PAIR"),
          label("item_payload", {
            prompt: "q",
            chosen: "a",
            rejected: "b",
            criteria: ["helpfulness"],
            annotator_rationale: null,
          }),
        ]),
      ),
    ).not.toBeNull();
  });

  it("AGENT_TRACE：steps 空或缺 action 拒绝，success 非布尔置 null", () => {
    const ok = resolveLlmItem(
      recordWith([
        label("item_type", "AGENT_TRACE"),
        label("item_payload", {
          steps: [{ action: "click", observation: null, tool: "mouse", result: "ok" }],
          task_goal: "整理桌面",
          success: "yes",
        }),
      ]),
    );
    expect(ok?.itemType).toBe("AGENT_TRACE");
    if (ok?.itemType === "AGENT_TRACE") {
      expect(ok.payload.success).toBeNull();
      expect(ok.payload.steps[0]?.tool).toBe("mouse");
    }
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "AGENT_TRACE"),
          label("item_payload", { steps: [] }),
        ]),
      ),
    ).toBeNull();
  });

  it("未知 item_type / 缺标签 / payload 缺失 → null（回退既有表单）", () => {
    expect(
      resolveLlmItem(recordWith([label("item_type", "VIDEO")])),
    ).toBeNull();
    expect(resolveLlmItem(recordWith([]))).toBeNull();
    expect(resolveLlmItem(recordWith([label("item_type", "TEXT")]))).toBeNull();
    expect(
      resolveLlmItem(
        recordWith([
          label("item_type", "TEXT"),
          label("item_payload", { wrong: 1 }),
        ]),
      ),
    ).toBeNull();
  });
});
