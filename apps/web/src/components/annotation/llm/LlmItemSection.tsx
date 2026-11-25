import { Space, Typography } from "antd";

import type { AnnotationRecord } from "../../../types/annotation";
import {
  resolveLlmItem,
  type LlmResolvedItem,
} from "../../../types/llmAnnotation";
import AgentTraceTree from "./AgentTraceTree";
import DialogueTurnList from "./DialogueTurnList";
import PreferencePairPanel from "./PreferencePairPanel";
import TextItemCard from "./TextItemCard";

/**
 * 按 item_type 分发 LLM 标注对象展示组件。
 * - 记录缺 `item_type` 标签、类型未知或 payload 结构不符 → 该记录回落
 *   既有表单区（本组件不渲染它），向后兼容，不改变既有数据流。
 * - 全部记录都不可解析时不渲染任何块（页面保持原样）。
 */
export default function LlmItemSection({
  records,
  preferenceValue,
  onPreferenceChange,
}: {
  records: AnnotationRecord[];
  preferenceValue?: Record<string, import("../../../types/llmAnnotation").PreferencePairAnnotationValue | null>;
  onPreferenceChange?: (
    annotationId: string,
    value: import("../../../types/llmAnnotation").PreferencePairAnnotationValue | null,
  ) => void;
}) {
  const resolved = records
    .map((record) => ({ record, item: resolveLlmItem(record) }))
    .filter(
      (entry): entry is { record: AnnotationRecord; item: LlmResolvedItem } =>
        entry.item !== null,
    );

  if (resolved.length === 0) {
    return null;
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {resolved.map(({ record, item }) => (
        <div key={record.annotation_id}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            标注对象 {record.annotation_id} · {item.itemType}
          </Typography.Text>
          {item.itemType === "TEXT" ? (
            <TextItemCard payload={item.payload} />
          ) : item.itemType === "DIALOGUE" ? (
            <DialogueTurnList turns={item.payload.turns} />
          ) : item.itemType === "AGENT_TRACE" ? (
            <AgentTraceTree payload={item.payload} />
          ) : item.itemType === "PREFERENCE_PAIR" ? (
            <PreferencePairPanel
              payload={item.payload}
              value={preferenceValue?.[record.annotation_id] ?? null}
              onChange={(value) =>
                onPreferenceChange?.(record.annotation_id, value)
              }
            />
          ) : null}
        </div>
      ))}
    </Space>
  );
}
