import { Card, Descriptions, Tag, Typography } from "antd";

import type { TextItemPayload } from "../../../types/llmAnnotation";

/**
 * TEXT 类型 LLM 标注对象展示：正文 + 来源/语言元信息。
 * 纯展示组件，不承载标注动作（动作由提交页既有表单负责）。
 */
export default function TextItemCard({ payload }: { payload: TextItemPayload }) {
  return (
    <Card size="small" title="文本对象（TEXT）" data-testid="llm-text-card">
      <Typography.Paragraph
        style={{ whiteSpace: "pre-wrap", marginBottom: 12 }}
        data-testid="llm-text-body"
      >
        {payload.text}
      </Typography.Paragraph>
      <Descriptions size="small" column={2}>
        <Descriptions.Item label="来源">
          {payload.source_ref ?? "—"}
        </Descriptions.Item>
        <Descriptions.Item label="语言">
          {payload.language ? <Tag>{payload.language}</Tag> : "—"}
        </Descriptions.Item>
      </Descriptions>
      {payload.metadata && Object.keys(payload.metadata).length > 0 ? (
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          元数据字段：{Object.keys(payload.metadata).join("、")}
        </Typography.Paragraph>
      ) : null}
    </Card>
  );
}
