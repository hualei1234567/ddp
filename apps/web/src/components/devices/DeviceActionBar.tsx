import { Alert, Button, Input, Modal, Space, Tooltip, Typography, message } from "antd";
import { useState } from "react";

import { ApiError } from "../../services/api";
import {
  DEVICE_ACTION_VALUES,
  isDeviceAction,
  type DeviceAction,
  type DeviceActionPayload,
} from "../../services/devices";

const UUID_PATTERN =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

const ACTION_LABELS: Record<DeviceAction, string> = {
  activate: "启用",
  "enter-maintenance": "进入维修",
  "report-fault": "上报故障",
  suspend: "挂起",
  retire: "退役",
};

interface DeviceActionBarProps {
  /**
   * 服务端 `allowed_actions`。
   *
   * 设备域契约（`schemas.stage1.yaml`）目前没有该字段，服务端也不返回；
   * 传入 `undefined` 时组件按契约声明的动作端点渲染，并在界面明确提示
   * 「服务端未下发动作集合」——非法迁移仍由服务端 409/422 拒绝。
   * 这不是按 `status` 推断状态机：动作集合是固定的公开端点，与当前状态无关。
   */
  allowedActions?: string[];
  entityLabel: string;
  busy?: boolean;
  /** 行内紧凑形态。 */
  compact?: boolean;
  /** 整体禁用原因（如离线）。 */
  disabledReason?: string | null;
  onAction: (
    action: DeviceAction,
    payload: DeviceActionPayload,
  ) => Promise<void>;
}

function resolveActions(allowedActions: string[] | undefined): DeviceAction[] {
  if (allowedActions === undefined) {
    return [...DEVICE_ACTION_VALUES];
  }
  const seen = new Set<string>();
  const result: DeviceAction[] = [];
  for (const action of allowedActions) {
    if (!isDeviceAction(action) || seen.has(action)) {
      continue;
    }
    seen.add(action);
    result.push(action);
  }
  return result;
}

/**
 * 设备 / 传感器 / 机器人共用的生命周期动作条。
 *
 * 请求体字段按服务端 `action_preconditions` 的要求收集：
 * - `enter-maintenance`：reason + maintenance_record_id；
 * - `report-fault`：fault_code（fault_detail 可选，occurred_at 由服务端补）；
 * - `suspend` / `retire`：reason；
 * - `activate`：无必填字段。
 * 前端只做表单引导，是否允许迁移以服务端为准。
 */
export default function DeviceActionBar({
  allowedActions,
  entityLabel,
  busy = false,
  compact = false,
  disabledReason = null,
  onAction,
}: DeviceActionBarProps) {
  const [pending, setPending] = useState<DeviceAction | null>(null);
  const [reason, setReason] = useState("");
  const [faultCode, setFaultCode] = useState("");
  const [faultDetail, setFaultDetail] = useState("");
  const [maintenanceRecordId, setMaintenanceRecordId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const actions = resolveActions(allowedActions);

  const resetDialog = () => {
    setPending(null);
    setReason("");
    setFaultCode("");
    setFaultDetail("");
    setMaintenanceRecordId("");
  };

  const requiredReason = (action: DeviceAction): boolean =>
    action === "enter-maintenance" ||
    action === "suspend" ||
    action === "retire";

  const validationError = (action: DeviceAction): string | null => {
    if (requiredReason(action) && reason.trim() === "") {
      return "该动作必须填写原因。";
    }
    if (action === "enter-maintenance") {
      const recordId = maintenanceRecordId.trim();
      if (recordId === "") {
        return "进入维修必须填写维修工单 ID（maintenance_record_id）。";
      }
      if (!UUID_PATTERN.test(recordId)) {
        return "维修工单 ID 必须是合法 UUID。";
      }
    }
    if (action === "report-fault" && faultCode.trim() === "") {
      return "上报故障必须填写故障码（fault_code）。";
    }
    return null;
  };

  const run = async (action: DeviceAction) => {
    const error = validationError(action);
    if (error) {
      messageApi.error(error);
      return;
    }
    const payload: DeviceActionPayload = {};
    if (reason.trim() !== "") {
      payload.reason = reason.trim();
    }
    if (action === "report-fault") {
      payload.fault_code = faultCode.trim();
      if (faultDetail.trim() !== "") {
        payload.fault_detail = faultDetail.trim();
      }
    }
    if (action === "enter-maintenance") {
      payload.maintenance_record_id = maintenanceRecordId.trim();
    }
    try {
      setSubmitting(true);
      await onAction(action, payload);
      messageApi.success(`${entityLabel}动作已执行：${ACTION_LABELS[action]}`);
      resetDialog();
    } catch (caught) {
      if (caught instanceof ApiError && caught.isConflict) {
        messageApi.error("状态已变化或版本冲突，请刷新后重试。");
      } else {
        messageApi.error(
          caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const buttons = actions.map((action) => {
    const disabled = Boolean(disabledReason) || busy;
    const button = (
      <Button
        key={action}
        size={compact ? "small" : "middle"}
        danger={action === "retire" || action === "report-fault"}
        disabled={disabled}
        loading={submitting && pending === action}
        onClick={() => {
          setPending(action);
          setReason("");
          setFaultCode("");
          setFaultDetail("");
          setMaintenanceRecordId("");
        }}
      >
        {ACTION_LABELS[action]}
      </Button>
    );
    return disabled && disabledReason ? (
      <Tooltip key={action} title={disabledReason}>
        <span>{button}</span>
      </Tooltip>
    ) : (
      button
    );
  });

  const needsReason = pending !== null && requiredReason(pending);
  const needsFault = pending === "report-fault";
  const needsMaintenance = pending === "enter-maintenance";

  return (
    <>
      {contextHolder}
      <Space direction="vertical" size={8} className="ddp-stack">
        {allowedActions === undefined ? (
          <Alert
            type="warning"
            showIcon
            message="服务端未下发 allowed_actions"
            description="设备域契约尚未声明该字段；以下为契约声明的全部生命周期动作，是否为合法迁移由服务端校验。"
          />
        ) : null}
        {actions.length === 0 ? (
          <Typography.Text type="secondary">
            当前状态没有可执行的动作（以服务端 allowed_actions 为准）。
          </Typography.Text>
        ) : (
          <Space wrap size={compact ? 4 : 8}>
            {buttons}
          </Space>
        )}
      </Space>
      <Modal
        title={pending ? `${ACTION_LABELS[pending]}：填写动作信息` : "动作信息"}
        open={pending !== null}
        okText="确认执行"
        cancelText="取消"
        confirmLoading={submitting}
        okButtonProps={{
          disabled: pending ? validationError(pending) !== null : true,
        }}
        onCancel={resetDialog}
        onOk={() => {
          if (pending) {
            void run(pending);
          }
        }}
      >
        <Space direction="vertical" size={12} className="ddp-stack">
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            动作会写入审计；原因、故障码与工单号由服务端校验后落库。
          </Typography.Paragraph>
          <div>
            <Typography.Text>原因{needsReason ? "（必填）" : "（可选）"}</Typography.Text>
            <Input.TextArea
              rows={3}
              maxLength={1000}
              value={reason}
              placeholder="描述现场事实与处理依据"
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          {needsFault ? (
            <div>
              <Typography.Text>故障码（必填）</Typography.Text>
              <Input
                maxLength={64}
                value={faultCode}
                placeholder="例如 MOTOR_OVERTEMP"
                onChange={(event) => setFaultCode(event.target.value)}
              />
              <Typography.Text>故障详情（可选）</Typography.Text>
              <Input.TextArea
                rows={2}
                maxLength={2000}
                value={faultDetail}
                placeholder="现象、影响范围与已采取的措施"
                onChange={(event) => setFaultDetail(event.target.value)}
              />
            </div>
          ) : null}
          {needsMaintenance ? (
            <div>
              <Typography.Text>维修工单 ID（必填）</Typography.Text>
              <Input
                value={maintenanceRecordId}
                placeholder="外部维修系统中的工单 UUID"
                onChange={(event) => setMaintenanceRecordId(event.target.value)}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                维修工单由外部系统管理，DDP 只保存引用；请粘贴工单 UUID。
              </Typography.Text>
            </div>
          ) : null}
        </Space>
      </Modal>
    </>
  );
}
