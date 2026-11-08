import { SendOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Input, Select, Space, Typography } from "antd";
import { useMemo, useState } from "react";

import {
  buildEpisodeActionInput,
  EMPTY_EPISODE_ACTION_FORM,
  EPISODE_ACTION_OPTIONS,
  QUALITY_STATUS_OPTIONS,
  type EpisodeActionFeedback,
} from "../../services/episodeActions";
import type {
  Episode,
  EpisodeAction,
  EpisodeActionInput,
} from "../../types/collection";

interface EpisodeActionPanelProps {
  episode: Episode;
  online: boolean;
  busy: boolean;
  feedback: EpisodeActionFeedback | null;
  onSubmit: (action: EpisodeAction, input: EpisodeActionInput) => void;
}

/**
 * Episode 动作表单。
 *
 * 不在前端维护状态机：全部动作都展示，由服务端按当前状态与权限裁决。
 * 只有动作必填项（Manifest、帧数、质检结果）在前端做输入有效性校验。
 */
export default function EpisodeActionPanel({
  episode,
  online,
  busy,
  feedback,
  onSubmit,
}: EpisodeActionPanelProps) {
  const [action, setAction] = useState<EpisodeAction>("quality-check");
  const [values, setValues] = useState(EMPTY_EPISODE_ACTION_FORM);
  const [validationError, setValidationError] = useState<string | null>(null);

  const selected = useMemo(
    () =>
      EPISODE_ACTION_OPTIONS.find((option) => option.action === action) ??
      EPISODE_ACTION_OPTIONS[0],
    [action],
  );

  const updateValue = (
    key: keyof typeof EMPTY_EPISODE_ACTION_FORM,
    value: string,
  ) => {
    setValues((previous) => ({ ...previous, [key]: value }));
    setValidationError(null);
  };

  const handleSubmit = () => {
    const result = buildEpisodeActionInput(action, values);
    if (result.error || result.input === null) {
      setValidationError(result.error);
      return;
    }
    setValidationError(null);
    onSubmit(action, result.input);
  };

  return (
    <Card
      title="状态动作"
      extra={
        <Typography.Text type="secondary">
          当前版本 v{episode.version}
        </Typography.Text>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        <Alert
          type="info"
          showIcon
          message="动作可用性以服务端为准"
          description="前端不根据 Episode 状态推断可执行动作；服务端会校验状态机、权限与 If-Match 版本。"
        />
        {!online ? (
          <Alert
            type="warning"
            showIcon
            message="当前离线"
            description="恢复网络后才能提交状态动作；本页不会离线排队写操作。"
          />
        ) : null}
        {feedback ? (
          <Alert
            type={feedback.kind}
            showIcon
            message={feedback.title}
            description={feedback.description}
          />
        ) : null}
        {validationError ? (
          <Alert type="error" showIcon message={validationError} />
        ) : null}

        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>动作</Typography.Text>
          <Select<EpisodeAction>
            value={action}
            options={EPISODE_ACTION_OPTIONS.map((option) => ({
              value: option.action,
              label: option.label,
            }))}
            onChange={(value) => {
              setAction(value);
              setValues(EMPTY_EPISODE_ACTION_FORM);
              setValidationError(null);
            }}
            style={{ maxWidth: 360 }}
          />
          <Typography.Text type="secondary">{selected?.hint}</Typography.Text>
        </Space>

        {action === "ready" ? (
          <>
            <Typography.Text strong>Manifest JSON（必填）</Typography.Text>
            <Input.TextArea
              rows={8}
              value={values.manifestText}
              placeholder='{"schema_version":"ddp.canonical/1.0.0-draft", ...}'
              onChange={(event) =>
                updateValue("manifestText", event.target.value)
              }
            />
            <Typography.Text strong>帧数（必填）</Typography.Text>
            <Input
              inputMode="numeric"
              value={values.frameCount}
              placeholder="例如 300"
              onChange={(event) =>
                updateValue("frameCount", event.target.value)
              }
              style={{ maxWidth: 200 }}
            />
            <Typography.Text type="secondary">
              Manifest 由解析 Worker 生成；READY 后数据字段不可变，Hash 由服务端计算。
            </Typography.Text>
          </>
        ) : null}

        {action === "quality-result" ? (
          <>
            <Typography.Text strong>质检结果（必填）</Typography.Text>
            <Select
              value={values.qualityStatus || undefined}
              placeholder="选择 PASS / WARN / FAIL"
              options={QUALITY_STATUS_OPTIONS}
              onChange={(value) => updateValue("qualityStatus", value ?? "")}
              style={{ maxWidth: 260 }}
            />
          </>
        ) : null}

        <Typography.Text strong>
          {action === "ready" ? "备注（可选）" : "原因 / 备注"}
        </Typography.Text>
        <Input.TextArea
          rows={2}
          value={values.reason}
          placeholder="建议记录操作原因，便于审计追溯。"
          onChange={(event) => updateValue("reason", event.target.value)}
        />

        <Button
          type="primary"
          icon={<SendOutlined />}
          loading={busy}
          disabled={!online || busy}
          onClick={handleSubmit}
        >
          提交动作
        </Button>
      </Space>
    </Card>
  );
}
