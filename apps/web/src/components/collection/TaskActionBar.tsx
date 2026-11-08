import { Alert, Button, Input, Modal, Space, Tooltip, Typography } from "antd";
import { useState } from "react";

import type { CollectionAction } from "../../services/collectionPresentation";

interface TaskActionBarProps {
  actions: { action: CollectionAction; label: string; requiresReason: boolean }[];
  busyAction?: string | null;
  /** 所有写操作不可用的整体原因（如离线）。 */
  disabledReason?: string | null;
  /** 单个动作的附加禁用原因，例如开工检查未完成。 */
  getActionDisabledReason?: (action: string) => string | null;
  onAction: (action: string, reason: string | null) => void;
}

/**
 * 服务端动作白名单按钮。缺失 allowed_actions 时全部禁用并说明原因，
 * 前端绝不根据 status 自行推断动作。
 */
export default function TaskActionBar({
  actions,
  busyAction = null,
  disabledReason = null,
  getActionDisabledReason,
  onAction,
}: TaskActionBarProps) {
  const [pendingAction, setPendingAction] = useState<{
    action: string;
    label: string;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);

  if (actions.length === 0) {
    return (
      <Alert
        type="info"
        showIcon
        message="服务端未下发可执行动作"
        description="阶段 2 契约草案尚未返回 allowed_actions 时，前端不根据任务状态推断动作；请等待服务端返回或刷新。"
      />
    );
  }

  const closeReasonDialog = () => {
    setPendingAction(null);
    setReason("");
    setReasonError(null);
  };

  const trigger = (action: string, label: string, requiresReason: boolean) => {
    if (requiresReason) {
      setPendingAction({ action, label });
      setReason("");
      setReasonError(null);
      return;
    }
    onAction(action, null);
  };

  return (
    <>
      <Space wrap>
        {actions.map((entry) => {
          const actionDisabled =
            disabledReason ?? getActionDisabledReason?.(entry.action) ?? null;
          const button = (
            <Button
              key={entry.action}
              type={
                entry.action === "start" ||
                entry.action === "submit" ||
                entry.action === "approve"
                  ? "primary"
                  : "default"
              }
              danger={
                entry.action === "cancel" ||
                entry.action === "reject" ||
                entry.action === "block"
              }
              loading={busyAction === entry.action}
              disabled={Boolean(actionDisabled)}
              onClick={() =>
                trigger(entry.action, entry.label, entry.requiresReason)
              }
            >
              {entry.label}
            </Button>
          );
          return actionDisabled ? (
            <Tooltip key={entry.action} title={actionDisabled}>
              <span>{button}</span>
            </Tooltip>
          ) : (
            button
          );
        })}
      </Space>

      <Modal
        open={pendingAction !== null}
        title={pendingAction ? `${pendingAction.label}：填写原因` : "填写原因"}
        okText="确认提交"
        cancelText="取消"
        okButtonProps={{
          disabled: reason.trim() === "",
        }}
        onCancel={closeReasonDialog}
        onOk={() => {
          if (reason.trim() === "") {
            setReasonError("该动作必须填写原因。");
            return;
          }
          if (pendingAction) {
            onAction(pendingAction.action, reason.trim());
          }
          closeReasonDialog();
        }}
      >
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text type="secondary">
            原因会随动作提交到服务端并进入审计；请描述现场事实与处理依据。
          </Typography.Text>
          <Input.TextArea
            value={reason}
            rows={4}
            maxLength={1000}
            showCount
            status={reasonError ? "error" : undefined}
            onChange={(event) => {
              setReason(event.target.value);
              setReasonError(null);
            }}
            placeholder="例如：设备电量不足，已停止采集并等待更换电池"
          />
          {reasonError ? (
            <Typography.Text type="danger">{reasonError}</Typography.Text>
          ) : null}
        </Space>
      </Modal>
    </>
  );
}
