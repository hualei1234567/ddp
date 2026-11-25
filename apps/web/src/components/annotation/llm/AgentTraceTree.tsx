import { Card, Space, Steps, Tag, Typography } from "antd";

import {
  AGENT_STEP_LABEL_VALUES,
  type AgentStepLabel,
  type AgentTraceItemPayload,
} from "../../../types/llmAnnotation";

const LABEL_META: Record<
  AgentStepLabel,
  { color: string; reward: string }
> = {
  CORRECT: { color: "green", reward: "+1" },
  REDUNDANT: { color: "gold", reward: "-0.5" },
  HALLUCINATED_TOOL: { color: "orange", reward: "-1" },
  FATAL: { color: "red", reward: "-2" },
};

const LABEL_OPTIONS: AgentStepLabel[] = [...AGENT_STEP_LABEL_VALUES];

/**
 * AGENT_TRACE 类型展示：步骤树 + 四分类标签（蓝图 N1）。
 * reward 值域与契约 StepMarkOut 一致，仅展示；标注写入走既有表单/PRM 流程。
 */
export default function AgentTraceTree({ payload }: { payload: AgentTraceItemPayload }) {
  return (
    <Card size="small" title={`智能体轨迹（AGENT_TRACE，共 ${payload.steps.length} 步）`} data-testid="llm-trace-card">
      {payload.task_goal ? (
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          任务目标：{payload.task_goal}
        </Typography.Paragraph>
      ) : null}
      {payload.success !== null ? (
        <Typography.Paragraph style={{ marginBottom: 8 }}>
          执行结果：
          <Tag color={payload.success ? "green" : "red"}>
            {payload.success ? "成功" : "失败"}
          </Tag>
        </Typography.Paragraph>
      ) : null}
      <Steps
        direction="vertical"
        size="small"
        items={payload.steps.map((step, index) => {
          const toolLabel = step.tool;
          const observation = step.observation;
          const result = step.result;
          return {
            key: index,
            title: (
              <Space size={8} wrap>
                <Typography.Text strong>{`步骤 ${index + 1}`}</Typography.Text>
                {toolLabel ? <Tag color="blue">{toolLabel}</Tag> : null}
              </Space>
            ),
            description: (
              <Space direction="vertical" size={4} className="ddp-stack">
                <Typography.Paragraph
                  style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                >
                  {step.action}
                </Typography.Paragraph>
                {observation ? (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ marginBottom: 0 }}
                  >
                    观察：{observation}
                  </Typography.Paragraph>
                ) : null}
                {result ? (
                  <Typography.Paragraph
                    type="secondary"
                    style={{ marginBottom: 0 }}
                  >
                    结果：{result}
                  </Typography.Paragraph>
                ) : null}
                <Space size={4} wrap data-testid={`llm-trace-labels-${index}`}>
                  {LABEL_OPTIONS.map((label) => (
                    <Tag key={label} color={LABEL_META[label].color}>
                      {`${label}（${LABEL_META[label].reward}）`}
                    </Tag>
                  ))}
                </Space>
              </Space>
            ),
          };
        })}
      />
    </Card>
  );
}
