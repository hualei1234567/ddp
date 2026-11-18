import { Tag, Tooltip } from "antd";

import type {
  QualityPresentation,
  QualityTone,
} from "../../services/qualityRunPresentation";

const TONE_COLOR: Record<QualityTone, string | undefined> = {
  success: "green",
  error: "red",
  warning: "orange",
  neutral: "default",
};

/** 质量结论标签：颜色只表达语气，文案由 presentation 决定（不在此处判定）。 */
export default function QualityToneTag({
  presentation,
  hint,
}: {
  presentation: QualityPresentation;
  hint?: string;
}) {
  const tag = (
    <Tag color={TONE_COLOR[presentation.tone]}>{presentation.label}</Tag>
  );
  return hint ? <Tooltip title={hint}>{tag}</Tooltip> : tag;
}
