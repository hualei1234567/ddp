import { Button, Checkbox, Input, Modal, Space, Typography, message } from "antd";
import { useState } from "react";

import {
  ACTIONS_FALLBACK_NOTE,
  presentAnnotationAction,
  requiresAnnotationReason,
  type AnnotationActionKind,
} from "../../services/annotationActions";

export interface AnnotationActionPayload {
  reason: string | null;
  /** 仅抽检 `assign` 使用；其它动作忽略。 */
  reviewerId?: string | null;
  /** 仅任务 `complete` 验收未达标时需要；其它动作忽略。 */
  waiverReason?: string | null;
  /** 仅样本集 `preview` 的「已确认数据来源风险」开关。 */
  warnConfirmed?: boolean;
}

interface AnnotationActionButtonsProps {
  kind: AnnotationActionKind;
  /** 要渲染的动作；由页面通过 `resolveAnnotationActions` 解析（优先服务端）。 */
  actions: readonly string[];
  busyAction?: string | null;
  size?: "small" | "middle";
  /** 需要复核人 ID 的动作（抽检 `assign`）。 */
  reviewerRequiredActions?: readonly string[];
  /** 可附带豁免原因的动作（任务 `complete`）。 */
  waiverActions?: readonly string[];
  /** 可确认数据来源风险的动作（样本集 `preview`）。 */
  warnActions?: readonly string[];
  /** 动作失败时组件会用 `error.message` 提示，页面可同时展示详情。 */
  onAction: (action: string, payload: AnnotationActionPayload) => Promise<void>;
  /** 是否展示「按状态兜底」说明；列表行内默认关闭。 */
  showFallbackNote?: boolean;
}

/**
 * 标注链路统一动作条。
 *
 * 按钮集合完全由 `actions` 决定；本组件不根据状态自行增删按钮。
 * 原因/复核人校验只做「缺字段时提前提示」，权限与状态迁移仍以服务端为准。
 */
export default function ActionButtons({
  kind,
  actions,
  busyAction = null,
  size = "middle",
  reviewerRequiredActions = [],
  waiverActions = [],
  warnActions = [],
  onAction,
  showFallbackNote = false,
}: AnnotationActionButtonsProps) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  const [waiverReason, setWaiverReason] = useState("");
  const [warnConfirmed, setWarnConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  if (actions.length === 0) {
    return (
      <Space direction="vertical" size={0}>
        <Typography.Text type="secondary">当前状态没有可执行动作。</Typography.Text>
        {showFallbackNote ? (
          <Typography.Text type="secondary">{ACTIONS_FALLBACK_NOTE}</Typography.Text>
        ) : null}
      </Space>
    );
  }

  const needsReviewer = (action: string) =>
    reviewerRequiredActions.includes(action);

  const allowsWaiver = (action: string) => waiverActions.includes(action);

  const allowsWarn = (action: string) => warnActions.includes(action);

  const run = async (action: string, payload: AnnotationActionPayload) => {
    try {
      setSubmitting(true);
      await onAction(action, payload);
      messageApi.success(`动作已执行：${presentAnnotationAction(action)}`);
      setPending(null);
      setReason("");
      setReviewerId("");
      setWaiverReason("");
      setWarnConfirmed(false);
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const trigger = (action: string) => {
    if (
      !requiresAnnotationReason(kind, action) &&
      !needsReviewer(action) &&
      !allowsWaiver(action) &&
      !allowsWarn(action)
    ) {
      void run(action, { reason: null });
      return;
    }
    setPending(action);
    setReason("");
    setReviewerId("");
    setWaiverReason("");
    setWarnConfirmed(false);
  };

  const pendingAction = pending;

  return (
    <>
      {contextHolder}
      <Space wrap size={8}>
        {actions.map((action) => (
          <Button
            key={action}
            size={size}
            danger={requiresAnnotationReason(kind, action)}
            loading={busyAction === action || submitting}
            onClick={() => trigger(action)}
          >
            {presentAnnotationAction(action)}
          </Button>
        ))}
      </Space>
      {showFallbackNote ? (
        <Typography.Text type="secondary">{ACTIONS_FALLBACK_NOTE}</Typography.Text>
      ) : null}
      <Modal
        title={
          pendingAction
            ? `确认执行：${presentAnnotationAction(pendingAction)}`
            : "确认执行"
        }
        open={pendingAction !== null}
        okText="确认"
        cancelText="取消"
        confirmLoading={submitting}
        onCancel={() => setPending(null)}
        onOk={() => {
          if (!pendingAction) {
            return;
          }
          if (
            requiresAnnotationReason(kind, pendingAction) &&
            reason.trim() === ""
          ) {
            messageApi.warning("请填写原因，服务端会写入审计。");
            return;
          }
          if (needsReviewer(pendingAction) && reviewerId.trim() === "") {
            messageApi.warning("请填写复核人 ID。");
            return;
          }
          void run(pendingAction, {
            reason: reason.trim() === "" ? null : reason.trim(),
            reviewerId: needsReviewer(pendingAction)
              ? reviewerId.trim()
              : null,
            waiverReason:
              allowsWaiver(pendingAction) && waiverReason.trim() !== ""
                ? waiverReason.trim()
                : null,
            warnConfirmed: allowsWarn(pendingAction) && warnConfirmed,
          });
        }}
      >
        {pendingAction && requiresAnnotationReason(kind, pendingAction) ? (
          <Space direction="vertical" size={8} className="ddp-stack">
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
          </Space>
        ) : (
          <Typography.Paragraph type="secondary">
            状态迁移由服务端裁决并写入审计。
          </Typography.Paragraph>
        )}
        {pendingAction && needsReviewer(pendingAction) ? (
          <Input
            value={reviewerId}
            placeholder="复核人 ID（项目成员 UUID）"
            onChange={(event) => setReviewerId(event.target.value)}
          />
        ) : null}
        {pendingAction && allowsWarn(pendingAction) ? (
          <Checkbox
            checked={warnConfirmed}
            onChange={(event) => setWarnConfirmed(event.target.checked)}
          >
            已确认数据来源与抽样风险提示
          </Checkbox>
        ) : null}
        {pendingAction && allowsWaiver(pendingAction) ? (
          <Space direction="vertical" size={4} className="ddp-stack">
            <Typography.Text type="secondary">
              验收数量未达标时，服务端要求填写豁免原因；已达标可留空。
            </Typography.Text>
            <Input.TextArea
              rows={2}
              maxLength={2000}
              value={waiverReason}
              placeholder="豁免原因（可选）"
              onChange={(event) => setWaiverReason(event.target.value)}
            />
          </Space>
        ) : null}
      </Modal>
    </>
  );
}
