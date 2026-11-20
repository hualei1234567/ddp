import { Button, Input, Modal, Space, Typography, message } from "antd";
import { useId, useState } from "react";

import PageState from "../ui/PageState";
import { ApiError } from "../../services/api";
import { resolveCaptureSessionActions } from "../../services/captureSessionPresentation";
import type { CaptureSessionAction } from "../../types/captureSession";

interface Props { allowedActions: string[]; busy?: boolean; onAction: (action: CaptureSessionAction, reason: string | null) => Promise<void>; }

export default function CaptureSessionActionBar({ allowedActions, busy = false, onAction }: Props) {
  const actions = resolveCaptureSessionActions(allowedActions);
  const [pending, setPending] = useState<(typeof actions)[number] | null>(null);
  const [reason, setReason] = useState("");
  const [running, setRunning] = useState<CaptureSessionAction | null>(null);
  const reasonId = useId();
  const [messageApi, holder] = message.useMessage();

  if (actions.length === 0) return <PageState compact variant="disabled" title="当前会话没有可执行动作" description="会话可能已进入终态；以服务端 allowed_actions 为准。" />;

  const run = async (action: CaptureSessionAction, value: string | null) => {
    try { setRunning(action); await onAction(action, value); setPending(null); setReason(""); messageApi.success("会话动作已执行"); }
    catch (caught) { messageApi.error(caught instanceof ApiError && caught.isConflict ? "会话已更新，请刷新后重试。" : caught instanceof Error ? caught.message : "动作执行失败"); }
    finally { setRunning(null); }
  };

  return <>{holder}<Space wrap>{actions.map((entry) => <Button key={entry.action} danger={entry.danger} loading={running === entry.action} disabled={busy || running !== null} onClick={() => entry.requiresReason ? (setPending(entry), setReason("")) : void run(entry.action, null)}>{entry.label}</Button>)}</Space>
    <Modal title={pending ? `${pending.label}：填写原因` : "填写原因"} open={pending !== null} okText="确认提交" cancelText="取消" confirmLoading={running !== null} okButtonProps={{ disabled: reason.trim() === "" }} onCancel={() => setPending(null)} onOk={() => pending && reason.trim() !== "" && void run(pending.action, reason.trim())}>
      <label htmlFor={reasonId}>动作原因</label><Typography.Paragraph type="secondary">请记录现场事实；原因将提交服务端审计。</Typography.Paragraph>
      <Input.TextArea id={reasonId} rows={3} maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} />
    </Modal></>;
}
