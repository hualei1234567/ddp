import { DownOutlined, RightOutlined } from "@ant-design/icons";
import { Button, Card, Space, Tag, Typography } from "antd";
import { useState } from "react";

import type { DialogueTurn } from "../../../types/llmAnnotation";

/** 长输出折叠阈值：超过该长度的 turn 内容默认收起。 */
const COLLAPSE_LENGTH = 200;

const ROLE_COLOR: Record<string, string> = {
  user: "blue",
  assistant: "green",
  system: "purple",
  tool: "orange",
};

/**
 * DIALOGUE 类型展示：角色徽标 + 可折叠长输出。
 * 长内容默认收起，点击展开完整文本；短内容直接平铺。
 */
export default function DialogueTurnList({
  turns,
}: {
  turns: DialogueTurn[];
}) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  return (
    <Card size="small" title={`对话对象（DIALOGUE，共 ${turns.length} 轮）`} data-testid="llm-dialogue-card">
      <Space direction="vertical" size={8} className="ddp-stack">
        {turns.map((turn, index) => {
          const isLong = turn.content.length > COLLAPSE_LENGTH;
          const isOpen = expanded[index] ?? false;
          const shown = isLong && !isOpen
            ? `${turn.content.slice(0, COLLAPSE_LENGTH)}…`
            : turn.content;
          return (
            <div key={index} data-testid={`llm-dialogue-turn-${index}`}>
              <Space size={8} wrap>
                <Tag color={ROLE_COLOR[turn.role] ?? "default"}>
                  {`#${index + 1} ${turn.role}`}
                </Tag>
                {turn.ts ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {turn.ts}
                  </Typography.Text>
                ) : null}
              </Space>
              <Typography.Paragraph
                style={{ whiteSpace: "pre-wrap", marginBottom: 4 }}
                data-testid={`llm-dialogue-content-${index}`}
              >
                {shown}
              </Typography.Paragraph>
              {isLong ? (
                <Button
                  type="link"
                  size="small"
                  icon={isOpen ? <DownOutlined /> : <RightOutlined />}
                  onClick={() =>
                    setExpanded((prev) => ({ ...prev, [index]: !isOpen }))
                  }
                  data-testid={`llm-dialogue-toggle-${index}`}
                >
                  {isOpen ? "收起" : "展开全文"}
                </Button>
              ) : null}
            </div>
          );
        })}
      </Space>
    </Card>
  );
}
