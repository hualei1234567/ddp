import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import { ApiError } from "../../services/api";
import {
  presentGoldSetAction,
  type QualityActionOption,
} from "../../services/qualityPresentation";

interface GoldSetActionBarProps {
  /**
   * 要渲染的动作。
   *
   * `GoldSetOut` 没有 `allowed_actions` 字段，契约也没有黄金集的状态迁移表，
   * 因此由页面传入契约声明的动作集（`GOLD_SET_ACTIONS`），不按 `status`
   * 自行推断可用性——那等于在前端复制一遍后端状态机。
   */
  actions: readonly QualityActionOption[];
  busy?: boolean;
  onAction: (action: string, reason: string | null) => Promise<void>;
}

/**
 * 黄金集状态动作条。
 *
 * 非法迁移由服务端拒绝（409/422），页面如实展示错误并提示刷新，
 * 而不是提前隐藏按钮——隐藏会让用户误以为"这个状态就是没有后续动作"。
 * `GoldSetActionRequest.reason` 在契约里可选；部分动作服务端要求原因，
 * 缺失时由服务端返回 422，错误信息会透出。
 */
export default function GoldSetActionBar({
  actions,
  busy = false,
  onAction,
}: GoldSetActionBarProps) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  if (actions.length === 0) {
    return (
      <Typography.Text type="secondary">
        没有可用的黄金集动作。
      </Typography.Text>
    );
  }

  const run = async (action: string, reasonValue: string | null) => {
    try {
      setSubmitting(true);
      await onAction(action, reasonValue);
      messageApi.success(`动作已执行：${presentGoldSetAction(action)}`);
      setPending(null);
      setReason("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("该黄金集状态已变化或当前状态不允许此动作，请刷新后重试。");
      } else if (caught instanceof ApiError && caught.status === 422) {
        messageApi.error(
          `服务端拒绝该动作：${caught.message}（部分动作要求填写原因）`,
        );
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const pendingOption = actions.find((item) => item.action === pending);

  return (
    <>
      {contextHolder}
      <Space wrap>
        {actions.map((option) => (
          <Button
            key={option.action}
            danger={option.danger}
            loading={busy || submitting}
            onClick={() => {
              setPending(option.action);
              setReason("");
            }}
          >
            {option.label}
          </Button>
        ))}
      </Space>
      <Modal
        title={`确认执行：${pendingOption ? pendingOption.label : ""}`}
        open={pending !== null}
        okText="确认"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setPending(null)}
        onOk={() => {
          if (!pending) {
            return;
          }
          const trimmed = reason.trim();
          void run(pending, trimmed === "" ? null : trimmed);
        }}
      >
        <Typography.Paragraph type="secondary">
          状态迁移由服务端裁决并写入审计。原因可选，但部分动作服务端要求必填。
        </Typography.Paragraph>
        <Input.TextArea
          rows={2}
          maxLength={2000}
          value={reason}
          placeholder="原因（可选，最多 2000 字符）"
          onChange={(event) => setReason(event.target.value)}
        />
      </Modal>
    </>
  );
}
