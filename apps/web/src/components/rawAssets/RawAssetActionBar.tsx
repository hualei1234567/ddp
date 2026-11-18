import { Button, Input, Modal, Select, Space, Typography, message } from "antd";
import { useState } from "react";

import {
  RAW_ASSET_QUARANTINE_CODE_VALUES,
  type RawAssetQuarantineCode,
} from "../../types/collection";

export interface RawAssetActionPayload {
  reason: string | null;
  quarantineCodes?: string[];
}

interface RawAssetActionBarProps {
  /** 服务端下发的 `allowed_actions`；组件不自行推断。 */
  actions: readonly string[];
  busyAction?: string | null;
  onAction: (action: string, payload: RawAssetActionPayload) => Promise<void>;
}

interface ActionMeta {
  label: string;
  hint: string;
  danger?: boolean;
  /**
   * `reason`：必须填原因；`quarantine`：原因 + 至少一个隔离码；
   * `confirm`：不可逆，二次确认但不填原因。
   */
  form: "reason" | "quarantine" | "confirm" | "none";
}

const ACTION_META: Record<string, ActionMeta> = {
  "release-quarantine-or-revalidate": {
    label: "隔离放行并重新校验",
    hint: "清空本轮隔离结论，回到校验队列重新判定。",
    form: "none",
  },
  "reject-quarantined": {
    label: "驳回隔离资产",
    hint: "判定资产不可用，保留元数据与血缘。",
    danger: true,
    form: "reason",
  },
  "post-hoc-quarantine": {
    label: "事后隔离",
    hint: "已校验通过的资产发现问题时隔离，必须给出原因与隔离码。",
    danger: true,
    form: "quarantine",
  },
  archive: {
    label: "归档",
    hint: "正常生命周期终点；内容仍可下载，历史保留。",
    form: "confirm",
  },
  tombstone: {
    label: "墓碑化",
    hint: "只保留元数据与血缘，下载将被拒绝；不可撤销。",
    danger: true,
    form: "reason",
  },
};

/**
 * 原始资产动作条。
 *
 * 按钮完全来自服务端 `allowed_actions`：隔离资产出现"放行/驳回"，
 * 可用资产出现"归档/事后隔离/墓碑"。此前这些能力只有领域状态机、
 * 没有入口，界面只能展示用户永远造不出来的状态。
 */
export default function RawAssetActionBar({
  actions,
  busyAction = null,
  onAction,
}: RawAssetActionBarProps) {
  const [pending, setPending] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [codes, setCodes] = useState<RawAssetQuarantineCode[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const known = actions.filter((action) => ACTION_META[action] !== undefined);
  if (known.length === 0) {
    return (
      <Typography.Text type="secondary">当前状态没有可执行动作。</Typography.Text>
    );
  }

  const meta = (action: string): ActionMeta => ACTION_META[action] as ActionMeta;

  const submit = async () => {
    if (!pending) {
      return;
    }
    const form = meta(pending).form;
    if (form === "reason" && reason.trim() === "") {
      messageApi.warning("请填写原因");
      return;
    }
    if (form === "quarantine") {
      if (reason.trim() === "") {
        messageApi.warning("请填写原因");
        return;
      }
      if (codes.length === 0) {
        messageApi.warning("请至少选择一个隔离码");
        return;
      }
    }
    try {
      setSubmitting(true);
      await onAction(pending, {
        reason: reason.trim() === "" ? null : reason.trim(),
        quarantineCodes: form === "quarantine" ? codes : undefined,
      });
      messageApi.success(`动作已执行：${meta(pending).label}`);
      setPending(null);
      setReason("");
      setCodes([]);
    } catch (caught) {
      messageApi.error(
        caught instanceof Error ? caught.message : "动作执行失败，请稍后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space wrap>
      {contextHolder}
      {known.map((action) => {
        const info = meta(action);
        return (
          <Button
            key={action}
            danger={info.danger}
            loading={busyAction === action}
            onClick={() => {
              setPending(action);
              setReason("");
              setCodes([]);
            }}
          >
            {info.label}
          </Button>
        );
      })}
      <Modal
        open={pending !== null}
        title={pending ? meta(pending).label : ""}
        okText="确认执行"
        okButtonProps={{
          danger: pending ? meta(pending).danger : false,
          loading: submitting,
        }}
        cancelText="取消"
        onOk={() => void submit()}
        onCancel={() => {
          setPending(null);
          setReason("");
          setCodes([]);
        }}
      >
        {pending ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              {meta(pending).hint}
            </Typography.Text>
            {meta(pending).form === "reason" ||
            meta(pending).form === "quarantine" ? (
              <Input.TextArea
                rows={3}
                maxLength={1000}
                placeholder="必填：说明原因，写入审计"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            ) : null}
            {meta(pending).form === "quarantine" ? (
              <Select
                mode="multiple"
                allowClear
                style={{ width: "100%" }}
                placeholder="选择隔离码（至少一个）"
                value={codes}
                onChange={(value: RawAssetQuarantineCode[]) => setCodes(value)}
                options={RAW_ASSET_QUARANTINE_CODE_VALUES.map((value) => ({
                  value,
                  label: value,
                }))}
              />
            ) : null}
            {meta(pending).form === "confirm" ? (
              <Typography.Text type="warning">
                该动作不可撤销，请确认资产已不再需要作为活跃数据使用。
              </Typography.Text>
            ) : null}
          </Space>
        ) : null}
      </Modal>
    </Space>
  );
}
