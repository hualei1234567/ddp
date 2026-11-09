import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import { presentAction } from "../../services/datasetPresentation";

const REASON_REQUIRED = new Set([
  "archive",
  "revoke",
  "cancel",
  "reject",
  "block",
  "close",
]);

export interface ActionPayload {
  reason?: string | null;
  acceptance_opinion?: string | null;
  rework_ref?: string | null;
}

/**
 * 只渲染服务端 `allowed_actions` 中的动作；前端不根据状态推断权限。
 * 高风险动作要求填写原因，交给服务端审计。
 */
export default function ActionButtons({
  actions,
  busy,
  onAction,
}: {
  actions: string[];
  busy?: boolean;
  onAction: (action: string, payload: ActionPayload) => Promise<void>;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  if (actions.length === 0) {
    return (
      <Typography.Text type="secondary">
        当前状态没有可用动作（以服务端 allowed_actions 为准）。
      </Typography.Text>
    );
  }

  const run = async (action: string, payload: ActionPayload) => {
    try {
      setSubmitting(true);
      await onAction(action, payload);
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

  const trigger = (action: string) => {
    if (REASON_REQUIRED.has(action)) {
      setPending(action);
      setReason("");
      return;
    }
    void run(action, { reason: null });
  };

  return (
    <>
      {contextHolder}
      <Space wrap>
        {actions.map((action) => (
          <Button
            key={action}
            size="small"
            danger={REASON_REQUIRED.has(action)}
            loading={busy || submitting}
            onClick={() => trigger(action)}
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
          void run(pending, { reason: reason.trim() });
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
