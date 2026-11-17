import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import { ApiError } from "../../services/api";
import {
  presentAssetAction,
} from "../../services/collectionAssetPresentation";

interface AssetActionBarProps {
  /**
   * 服务端返回的 `allowed_actions`。组件不读 `status`，
   * 因此不会出现「前端自己算一遍状态机」导致的按钮与后端漂移。
   */
  actions: string[];
  busy?: boolean;
  /** 列表行内的紧凑形态。 */
  compact?: boolean;
  onAction: (action: string, reason: string | null) => Promise<void>;
}

/**
 * 采集素材状态动作条。
 *
 * `ARCHIVED` 是终态，服务端返回空数组，因此这里不会渲染任何动作按钮。
 * 原因非必填（契约 `CollectionAssetActionRequest.reason` 可选），确认弹窗
 * 只作为误点保护。
 */
export default function AssetActionBar({
  actions,
  busy = false,
  compact = false,
  onAction,
}: AssetActionBarProps) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  if (actions.length === 0) {
    return compact ? (
      <Typography.Text type="secondary">无动作</Typography.Text>
    ) : (
      <Typography.Text type="secondary">
        当前状态没有可执行的动作（以服务端 allowed_actions 为准）。
      </Typography.Text>
    );
  }

  const run = async (action: string, reasonValue: string | null) => {
    try {
      setSubmitting(true);
      await onAction(action, reasonValue);
      messageApi.success(`动作已执行：${presentAssetAction(action)}`);
      setPending(null);
      setReason("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("该素材状态已变化或存在引用，请刷新后重试。");
      } else {
        messageApi.error(
          caught instanceof Error
            ? caught.message
            : "动作执行失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const pendingAction = pending;

  return (
    <>
      {contextHolder}
      <Space wrap size={compact ? 4 : 8}>
        {actions.map((action) => (
          <Button
            key={action}
            size={compact ? "small" : "middle"}
            danger={action === "archive"}
            loading={busy || submitting}
            onClick={() => {
              setPending(action);
              setReason("");
            }}
          >
            {presentAssetAction(action)}
          </Button>
        ))}
      </Space>
      <Modal
        title={`确认执行：${pendingAction ? presentAssetAction(pendingAction) : ""}`}
        open={pendingAction !== null}
        okText="确认"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setPending(null)}
        onOk={() => {
          if (pendingAction) {
            const trimmed = reason.trim();
            void run(pendingAction, trimmed === "" ? null : trimmed);
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          动作由服务端校验状态迁移并写入审计；原因可不填。
        </Typography.Paragraph>
        <Input.TextArea
          rows={2}
          maxLength={1000}
          value={reason}
          placeholder="原因（可选）"
          onChange={(event) => setReason(event.target.value)}
        />
      </Modal>
    </>
  );
}
