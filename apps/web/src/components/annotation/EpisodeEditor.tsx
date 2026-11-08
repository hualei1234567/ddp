import { Card, Space, Typography } from "antd";

import type {
  DraftFieldValue,
  EpisodeFormValues,
} from "../../services/annotationValidation";
import type { LabelDefinition } from "../../types/annotation";
import type { Episode } from "../../types/collection";
import IdentifierValue from "../ui/IdentifierValue";
import SchemaForm from "./SchemaForm";

interface EpisodeEditorProps {
  episode: Episode;
  definitions: LabelDefinition[];
  values: EpisodeFormValues;
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (labelKey: string, value: DraftFieldValue) => void;
}

export default function EpisodeEditor({
  episode,
  definitions,
  values,
  errors,
  disabled,
  onChange,
}: EpisodeEditorProps) {
  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <span>{episode.code}</span>
          <Typography.Text type="secondary">
            Episode v{episode.episode_version}
          </Typography.Text>
        </Space>
      }
      extra={<IdentifierValue value={episode.episode_id} label="Episode ID" />}
    >
      <Space direction="vertical" size={10} className="ddp-stack">
        <Typography.Text type="secondary">
          时间范围：{episode.start_time_ns} – {episode.end_time_ns} ns · 时域{" "}
          {episode.time_domain}
        </Typography.Text>
        <SchemaForm
          definitions={definitions}
          values={values}
          errors={errors}
          disabled={disabled}
          onChange={onChange}
        />
      </Space>
    </Card>
  );
}
