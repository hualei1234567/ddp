import { Tag } from "antd";

import type { StatusPresentation } from "../../services/datasetPresentation";

export default function StatusTag({
  value,
  presentation,
  prefix,
}: {
  value: string | null | undefined;
  presentation: (value: string) => StatusPresentation;
  prefix?: string;
}) {
  if (!value) {
    return <Tag>未设置</Tag>;
  }
  const { label, color } = presentation(value);
  return (
    <Tag color={color}>
      {prefix ? `${prefix}：` : ""}
      {label}
    </Tag>
  );
}
