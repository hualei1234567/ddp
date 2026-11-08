import { Space, Typography } from "antd";

import type {
  DraftFieldValue,
  EpisodeFormValues,
} from "../../services/annotationValidation";
import type { LabelDefinition } from "../../types/annotation";
import SchemaField from "./SchemaField";

interface SchemaFormProps {
  definitions: LabelDefinition[];
  values: EpisodeFormValues;
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (labelKey: string, value: DraftFieldValue) => void;
}

export default function SchemaForm({
  definitions,
  values,
  errors,
  disabled,
  onChange,
}: SchemaFormProps) {
  if (definitions.length === 0) {
    return (
      <Typography.Text type="secondary">
        当前 Schema 没有标注员可见的标签；提交前请联系标注管理员检查 Schema。
      </Typography.Text>
    );
  }
  return (
    <Space direction="vertical" size={14} className="ddp-stack">
      {definitions.map((definition) => (
        <SchemaField
          key={definition.label_key}
          definition={definition}
          value={values[definition.label_key]}
          disabled={disabled}
          error={errors[definition.label_key]}
          onChange={(value) => onChange(definition.label_key, value)}
        />
      ))}
    </Space>
  );
}
