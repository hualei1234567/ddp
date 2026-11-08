import { Tag } from "antd";

import { presentAnnotationStatus } from "../../services/annotationPresentation";

const TONE_COLOR = {
  default: undefined,
  processing: "blue",
  success: "green",
  warning: "orange",
  error: "red",
} as const;

interface AnnotationStatusTagProps {
  value: string | null | undefined;
  /** 可选前缀，例如“任务”“提交”。 */
  prefix?: string;
}

export default function AnnotationStatusTag({
  value,
  prefix,
}: AnnotationStatusTagProps) {
  const presentation = presentAnnotationStatus(value);
  return (
    <Tag color={TONE_COLOR[presentation.tone]}>
      {prefix ? `${prefix}：${presentation.label}` : presentation.label}
    </Tag>
  );
}
