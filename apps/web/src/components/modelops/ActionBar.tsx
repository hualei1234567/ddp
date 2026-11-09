import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import {
  presentAction,
  requiresReason,
} from "../../services/modelopsPresentation";

/**
 * 只渲染服务端 `allowed_actions`；前端不按状态推断动作。
 * 需要附加参数的动作（如转需求、标记满足）由页面用 `skipActions` 排除后自行渲染。
 */
export default function ActionBar({
  actions,
  busy,
  skipActions,
  onAction,
}: {
  actions: string[];
  busy?: boolean;
  skipActions?: string[];
  onAction: (action: string, payload: { reason: string | null }) => Promise<void>;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const visible = actions.filter((action) => !(skipActions ?? []).includes(action));
  if (visible.length === 0) {
    return (
      <Typography.Text type="secondary">
        当前状态没有可直接执行的动作（以服务端 allowed_actions 为准）。
      </Typography.Text>
    );
  }

  const run = async (action: string, value: string | null) => {
    try {
      setSubmitting(true);
      await onAction(action, { reason: value });
      messageApi.success(`动作已执行：${presentAction(action)}`);
      setPending(null);
      setReason("");
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {contextHolder}
      <Space wrap>
        {visible.map((action) => (
          <Button
            key={action}
            size="small"
            danger={requiresReason(action)}
            loading={busy || submitting}
            onClick={() => {
              if (requiresReason(action)) {
                setPending(action);
                setReason("");
                return;
              }
              void run(action, null);
            }}
          >
            {presentAction(action)}
          </Button>
        ))}
      </Space>
      <Modal
        title={`确认执行：${pending ? presentAction(pending) : ""}`}
        open={pending !== null}
        okText="确认"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setPending(null)}
        onOk={() => {
          if (!pending) {
            return;
          }
          if (reason.trim() === "") {
            messageApi.warning("请填写原因，服务端将写入审计。");
            return;
          }
          void run(pending, reason.trim());
        }}
      >
        <Typography.Paragraph type="secondary">
          该动作会写入审计并要求说明原因。
        </Typography.Paragraph>
        <Input.TextArea
          rows={3}
          maxLength={2000}
          value={reason}
          placeholder="原因（必填）"
          onChange={(event) => setReason(event.target.value)}
        />
      </Modal>
    </>
  );
}
