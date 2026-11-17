import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import { ApiError } from "../../services/api";
import { resolveBatchActions } from "../../services/collectionPresentation";

interface BatchActionBarProps {
  /**
   * 服务端返回的 `allowed_actions`。缺失时**不渲染任何按钮**：
   * 批次状态迁移与可用动作只以服务端为准，前端不按 status 推断。
   */
  allowedActions: string[] | undefined;
  busy?: boolean;
  /** 列表行内的紧凑形态。 */
  compact?: boolean;
  onAction: (action: string, reason: string | null) => Promise<void>;
}

/**
 * 采集批次状态动作条。
 *
 * 动作集合来自服务端 `allowed_actions`（`CollectionBatchOut`）；
 * 需要原因的动作（暂停 / 恢复 / 阻塞 / 驳回 / 返工 / 取消等）
 * 由 `collectionPresentation` 的同一张表判定，弹窗只做引导，服务端仍会校验。
 */
export default function BatchActionBar({
  allowedActions,
  busy = false,
  compact = false,
  onAction,
}: BatchActionBarProps) {
  const [pending, setPending] = useState<{
    action: string;
    label: string;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [running, setRunning] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const actions = resolveBatchActions(allowedActions);

  if (!allowedActions) {
    return compact ? (
      <Typography.Text type="secondary">动作集合未下发</Typography.Text>
    ) : (
      <Typography.Text type="secondary">
        服务端未下发 allowed_actions；前端不根据批次状态推断动作，请刷新或联系服务端。
      </Typography.Text>
    );
  }

  if (actions.length === 0) {
    return compact ? (
      <Typography.Text type="secondary">无动作</Typography.Text>
    ) : (
      <Typography.Text type="secondary">
        当前状态没有可执行的批次动作（以服务端 allowed_actions 为准）。
      </Typography.Text>
    );
  }

  const run = async (action: string, reasonValue: string | null) => {
    try {
      setRunning(action);
      await onAction(action, reasonValue);
      messageApi.success("批次动作已执行");
      setPending(null);
      setReason("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("批次已被更新或当前状态不允许该动作，请刷新后重试。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
        );
      }
    } finally {
      setRunning(null);
    }
  };

  const trigger = (action: string, label: string, requiresReason: boolean) => {
    if (!requiresReason) {
      void run(action, null);
      return;
    }
    setPending({ action, label });
    setReason("");
  };

  return (
    <>
      {contextHolder}
      <Space wrap size={compact ? 4 : 8}>
        {actions.map((entry) => (
          <Button
            key={entry.action}
            size={compact ? "small" : "middle"}
            danger={
              entry.action === "cancel" ||
              entry.action === "reject" ||
              entry.action === "block"
            }
            loading={running === entry.action}
            disabled={busy || (running !== null && running !== entry.action)}
            onClick={() =>
              trigger(entry.action, entry.label, entry.requiresReason)
            }
          >
            {entry.label}
          </Button>
        ))}
      </Space>
      <Modal
        title={pending ? `${pending.label}：填写原因` : "填写原因"}
        open={pending !== null}
        okText="确认提交"
        cancelText="取消"
        confirmLoading={running !== null}
        okButtonProps={{ disabled: reason.trim() === "" }}
        onCancel={() => setPending(null)}
        onOk={() => {
          if (pending && reason.trim() !== "") {
            void run(pending.action, reason.trim());
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          原因会随动作提交到服务端并进入审计，请描述现场事实。
        </Typography.Paragraph>
        <Input.TextArea
          rows={3}
          maxLength={1000}
          value={reason}
          placeholder="例如：设备故障导致本批次暂停"
          onChange={(event) => setReason(event.target.value)}
        />
      </Modal>
    </>
  );
}
