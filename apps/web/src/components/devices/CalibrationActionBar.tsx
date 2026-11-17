import { Button, Popconfirm, Space, Typography, message } from "antd";
import { useState } from "react";

import { ApiError } from "../../services/api";
import {
  CALIBRATION_ACTION_VALUES,
  isCalibrationAction,
  type CalibrationAction,
} from "../../services/devices";

const ACTION_LABELS: Record<CalibrationAction, string> = {
  submit: "提交校验",
  validate: "校验通过",
  reject: "驳回",
  activate: "生效",
  revoke: "作废",
  expire: "标记过期",
};

/** 需要二次确认的动作（终态或高风险）。 */
const CONFIRM_ACTIONS = new Set<CalibrationAction>([
  "reject",
  "activate",
  "revoke",
  "expire",
]);

interface CalibrationActionBarProps {
  /**
   * 服务端 `allowed_actions`。标定契约当前没有该字段；
   * 缺失时按契约声明的动作端点渲染并显式提示，迁移合法性仍由服务端判定。
   */
  allowedActions?: string[];
  busy?: boolean;
  compact?: boolean;
  onAction: (action: CalibrationAction) => Promise<void>;
}

function resolveActions(allowedActions: string[] | undefined): CalibrationAction[] {
  if (allowedActions === undefined) {
    return [...CALIBRATION_ACTION_VALUES];
  }
  const seen = new Set<string>();
  const result: CalibrationAction[] = [];
  for (const action of allowedActions) {
    if (!isCalibrationAction(action) || seen.has(action)) {
      continue;
    }
    seen.add(action);
    result.push(action);
  }
  return result;
}

/**
 * 标定记录状态动作条。
 *
 * 后端标定动作请求体为空，只要求 If-Match；原因不在契约里，因此不弹原因框，
 * 只对终态/高风险动作做二次确认。校验类动作需要服务端 `validate` 权限，
 * 无权限时返回 403，页面如实展示。
 */
export default function CalibrationActionBar({
  allowedActions,
  busy = false,
  compact = false,
  onAction,
}: CalibrationActionBarProps) {
  const [submitting, setSubmitting] = useState<CalibrationAction | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const actions = resolveActions(allowedActions);

  const run = async (action: CalibrationAction) => {
    try {
      setSubmitting(action);
      await onAction(action);
      messageApi.success(`标定动作已执行：${ACTION_LABELS[action]}`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("标定状态已变化，请刷新后重试。");
      } else if (caught instanceof ApiError && caught.isForbidden) {
        messageApi.error("无权执行该校验动作（需要校验权限）。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(null);
    }
  };

  if (actions.length === 0) {
    return (
      <Typography.Text type="secondary">
        当前状态没有可执行的标定动作（以服务端 allowed_actions 为准）。
      </Typography.Text>
    );
  }

  return (
    <>
      {contextHolder}
      <Space direction="vertical" size={4}>
        {allowedActions === undefined ? (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            服务端未下发 allowed_actions；以下为契约声明的全部标定动作，合法性由服务端校验。
          </Typography.Text>
        ) : null}
        <Space wrap size={compact ? 4 : 8}>
          {actions.map((action) => {
            const button = (
              <Button
                key={action}
                size={compact ? "small" : "middle"}
                danger={action === "revoke" || action === "reject"}
                loading={submitting === action}
                disabled={busy || (submitting !== null && submitting !== action)}
                onClick={() => {
                  if (!CONFIRM_ACTIONS.has(action)) {
                    void run(action);
                  }
                }}
              >
                {ACTION_LABELS[action]}
              </Button>
            );
            if (CONFIRM_ACTIONS.has(action)) {
              return (
                <Popconfirm
                  key={action}
                  title={`确认执行「${ACTION_LABELS[action]}」？`}
                  okText="确认"
                  cancelText="取消"
                  onConfirm={() => void run(action)}
                >
                  {button}
                </Popconfirm>
              );
            }
            return button;
          })}
        </Space>
      </Space>
    </>
  );
}
