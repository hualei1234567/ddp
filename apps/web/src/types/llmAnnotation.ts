/**
 * LLM 标注对象（V52-021/022）前端类型。
 *
 * 端点形态假设（契约 packages/contracts/fragments/schemas.stage10.yaml 尚无
 * `item_type` 字段，已 grep 确认）：LLM 标注对象通过现有 JSON 标签值承载
 * （MEM-DEC-002 决策），约定两个 label：
 * - `item_type`：字符串枚举 TEXT / DIALOGUE / PREFERENCE_PAIR / AGENT_TRACE
 * - `item_payload`：四种 payload 结构之一的 JSON 对象（design-plan §4.1.1）
 * 后端端点落地后仅需调整 `resolveLlmItem` 的来源，类型结构不变。
 *
 * 外部数据进入 UI 前必须经 `resolveLlmItem` 严格解析；解析失败或未知类型
 * 返回 null，由调用方回退既有表单（向后兼容，不吞异常不伪造数据）。
 */

import type { AnnotationRecord } from "./annotation";

export const LLM_ITEM_TYPE_VALUES = [
  "TEXT",
  "DIALOGUE",
  "PREFERENCE_PAIR",
  "AGENT_TRACE",
] as const;
export type LlmItemType = (typeof LLM_ITEM_TYPE_VALUES)[number];

/** design-plan §4.1.1 TEXT：{text, source_ref, language, metadata} */
export interface TextItemPayload {
  text: string;
  source_ref: string | null;
  language: string | null;
  metadata: Record<string, unknown> | null;
}

/** design-plan §4.1.1 DIALOGUE：{turns:[{role,content,ts}], context_ref} */
export interface DialogueTurn {
  role: string;
  content: string;
  ts: string | null;
}

export interface DialogueItemPayload {
  turns: DialogueTurn[];
  context_ref: string | null;
}

/** design-plan §4.1.1 PREFERENCE_PAIR：{prompt, chosen, rejected, criteria[], annotator_rationale} */
export interface PreferencePairItemPayload {
  prompt: string;
  chosen: string;
  rejected: string;
  criteria: string[];
  annotator_rationale: string | null;
}

/**
 * MEM-DEC-002 偏好对比标注 JSON 约定值（本卡只落 winner/confidence/rationale）。
 * 渲染必须按 payload.shown_order（存在时），不得假设 A 在前。
 */
export const PREFERENCE_WINNER_VALUES = ["A", "B", "TIE"] as const;
export type PreferenceWinner = (typeof PREFERENCE_WINNER_VALUES)[number];

export interface PreferencePairAnnotationValue {
  winner: PreferenceWinner;
  confidence: number | null;
  rationale: string | null;
}

/** design-plan §4.1.1 AGENT_TRACE：{steps:[{action,observation,tool,result}], task_goal, success} */
export interface AgentTraceStep {
  action: string;
  observation: string | null;
  tool: string | null;
  result: string | null;
}

export interface AgentTraceItemPayload {
  steps: AgentTraceStep[];
  task_goal: string | null;
  success: boolean | null;
}

/**
 * 步骤四分类标签：值域与契约 schemas.stage10.yaml `StepMarkOut.label`
 * enum 完全一致（CORRECT/REDUNDANT/HALLUCINATED_TOOL/FATAL）。
 */
export const AGENT_STEP_LABEL_VALUES = [
  "CORRECT",
  "REDUNDANT",
  "HALLUCINATED_TOOL",
  "FATAL",
] as const;
export type AgentStepLabel = (typeof AGENT_STEP_LABEL_VALUES)[number];

export type LlmItemPayload =
  | TextItemPayload
  | DialogueItemPayload
  | PreferencePairItemPayload
  | AgentTraceItemPayload;

/** 判别联合：itemType 与 payload 结构一一对应，供 UI 按类型收窄。 */
export type LlmResolvedItem =
  | { itemType: "TEXT"; payload: TextItemPayload }
  | { itemType: "DIALOGUE"; payload: DialogueItemPayload }
  | { itemType: "PREFERENCE_PAIR"; payload: PreferencePairItemPayload }
  | { itemType: "AGENT_TRACE"; payload: AgentTraceItemPayload };

export function isLlmItemType(value: unknown): value is LlmItemType {
  return (
    typeof value === "string" &&
    (LLM_ITEM_TYPE_VALUES as readonly string[]).includes(value)
  );
}

export function isPreferenceWinner(value: unknown): value is PreferenceWinner {
  return (
    typeof value === "string" &&
    (PREFERENCE_WINNER_VALUES as readonly string[]).includes(value)
  );
}

export function isAgentStepLabel(value: unknown): value is AgentStepLabel {
  return (
    typeof value === "string" &&
    (AGENT_STEP_LABEL_VALUES as readonly string[]).includes(value)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function asRecordOrNull(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function parseTextPayload(value: unknown): TextItemPayload | null {
  if (!isRecord(value) || typeof value.text !== "string") {
    return null;
  }
  return {
    text: value.text,
    source_ref: asString(value.source_ref),
    language: asString(value.language),
    metadata: asRecordOrNull(value.metadata),
  };
}

function parseDialoguePayload(value: unknown): DialogueItemPayload | null {
  if (!isRecord(value) || !Array.isArray(value.turns)) {
    return null;
  }
  const turns: DialogueTurn[] = [];
  for (const raw of value.turns) {
    if (!isRecord(raw) || typeof raw.role !== "string" || typeof raw.content !== "string") {
      return null;
    }
    turns.push({ role: raw.role, content: raw.content, ts: asString(raw.ts) });
  }
  if (turns.length === 0) {
    return null;
  }
  return { turns, context_ref: asString(value.context_ref) };
}

function parsePreferencePairPayload(value: unknown): PreferencePairItemPayload | null {
  if (
    !isRecord(value) ||
    typeof value.prompt !== "string" ||
    typeof value.chosen !== "string" ||
    typeof value.rejected !== "string"
  ) {
    return null;
  }
  return {
    prompt: value.prompt,
    chosen: value.chosen,
    rejected: value.rejected,
    criteria: asStringArray(value.criteria),
    annotator_rationale: asString(value.annotator_rationale),
  };
}

function parseAgentTracePayload(value: unknown): AgentTraceItemPayload | null {
  if (!isRecord(value) || !Array.isArray(value.steps)) {
    return null;
  }
  const steps: AgentTraceStep[] = [];
  for (const raw of value.steps) {
    if (!isRecord(raw) || typeof raw.action !== "string") {
      return null;
    }
    steps.push({
      action: raw.action,
      observation: asString(raw.observation),
      tool: asString(raw.tool),
      result: asString(raw.result),
    });
  }
  if (steps.length === 0) {
    return null;
  }
  return {
    steps,
    task_goal: asString(value.task_goal),
    success: typeof value.success === "boolean" ? value.success : null,
  };
}

function readLabelValue(record: AnnotationRecord, labelKey: string): unknown {
  const label = record.labels.find((item) => item.label_key === labelKey);
  return label ? label.value : undefined;
}

/**
 * 从标注记录解析 LLM 可标注对象。
 * - 无 `item_type` 标签、类型未知、payload 缺失或结构不符 → null
 *   （调用方回退既有表单；类型/结构层面的契约破坏不由展示组件兜底）。
 */
export function resolveLlmItem(
  record: AnnotationRecord,
): LlmResolvedItem | null {
  const typeValue = readLabelValue(record, "item_type");
  if (!isLlmItemType(typeValue)) {
    return null;
  }
  const raw = readLabelValue(record, "item_payload");
  // 每个分支显式构造判别联合成员，让 itemType 与 payload 类型绑定的收窄生效。
  switch (typeValue) {
    case "TEXT": {
      const payload = parseTextPayload(raw);
      return payload ? { itemType: "TEXT", payload } : null;
    }
    case "DIALOGUE": {
      const payload = parseDialoguePayload(raw);
      return payload ? { itemType: "DIALOGUE", payload } : null;
    }
    case "PREFERENCE_PAIR": {
      const payload = parsePreferencePairPayload(raw);
      return payload ? { itemType: "PREFERENCE_PAIR", payload } : null;
    }
    case "AGENT_TRACE": {
      const payload = parseAgentTracePayload(raw);
      return payload ? { itemType: "AGENT_TRACE", payload } : null;
    }
  }
}
