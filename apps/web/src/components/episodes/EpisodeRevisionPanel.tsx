import { BranchesOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Input, Space, Typography } from "antd";
import { useState } from "react";

import type { EpisodeActionFeedback } from "../../services/episodeActions";
import type { Episode } from "../../types/collection";

interface EpisodeRevisionPanelProps {
  episode: Episode;
  online: boolean;
  busy: boolean;
  feedback: EpisodeActionFeedback | null;
  onSubmit: (code: string) => void;
}

/**
 * Episode 修订：服务端创建新 episode_id / episode_version+1，
 * 并通过 supersedes_id 指向当前版本；旧版本保持不变。
 */
export default function EpisodeRevisionPanel({
  episode,
  online,
  busy,
  feedback,
  onSubmit,
}: EpisodeRevisionPanelProps) {
  const [code, setCode] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleSubmit = () => {
    const trimmed = code.trim();
    if (trimmed === "") {
      setValidationError("修订必须提供新的项目内唯一 Episode 编号。");
      return;
    }
    if (trimmed === episode.code) {
      setValidationError("修订编号不能与当前版本相同。");
      return;
    }
    setValidationError(null);
    onSubmit(trimmed);
  };

  return (
    <Card
      title="创建修订"
      extra={
        <Typography.Text type="secondary">
          基于 v{episode.version} · If-Match
        </Typography.Text>
      }
    >
      <Space direction="vertical" size={12} className="ddp-stack">
        <Alert
          type="info"
          showIcon
          message="修订会生成新的 Episode 版本"
          description="服务端保持旧版本不变，新版本通过 supersedes_id 建立谱系；是否允许修订以服务端状态机为准。"
        />
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
        <Typography.Text strong>新 Episode 编号</Typography.Text>
        <Input
          value={code}
          placeholder={`例如 ${episode.code}-R2`}
          maxLength={64}
          onChange={(event) => {
            setCode(event.target.value);
            setValidationError(null);
          }}
          style={{ maxWidth: 360 }}
        />
        <Button
          type="primary"
          icon={<BranchesOutlined />}
          loading={busy}
          disabled={!online || busy}
          onClick={handleSubmit}
        >
          创建修订
        </Button>
      </Space>
    </Card>
  );
}
