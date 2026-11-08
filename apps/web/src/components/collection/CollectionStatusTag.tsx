import { Tag, Tooltip } from "antd";

import {
  getAnnotationStatus,
  getBatchStatus,
  getEpisodeStatus,
  getPriority,
  getQualityStatus,
  getTaskStatus,
  getUploadStatus,
  getVerificationStatus,
  type StatusPresentation,
} from "../../services/collectionPresentation";

export type CollectionStatusKind =
  | "task"
  | "batch"
  | "upload"
  | "verification"
  | "episode"
  | "quality"
  | "annotation"
  | "priority";

interface CollectionStatusTagProps {
  kind: CollectionStatusKind;
  value: string;
  /** 服务端或页面判定数据可能过期时，原状态标签不得继续显示为正常。 */
  stale?: boolean;
  staleHint?: string;
}

function resolve(kind: CollectionStatusKind, value: string): StatusPresentation {
  switch (kind) {
    case "task":
      return getTaskStatus(value);
    case "batch":
      return getBatchStatus(value);
    case "upload":
      return getUploadStatus(value);
    case "verification":
      return getVerificationStatus(value);
    case "episode":
      return getEpisodeStatus(value);
    case "quality":
      return getQualityStatus(value);
    case "annotation":
      return getAnnotationStatus(value);
    case "priority":
      return getPriority(value);
  }
}

/**
 * 阶段 2 状态标签。只展示服务端返回值；未知值保留原始码并提示，
 * 不用状态值决定权限或动作。
 */
export default function CollectionStatusTag({
  kind,
  value,
  stale = false,
  staleHint,
}: CollectionStatusTagProps) {
  const presentation = resolve(kind, value);
  const label = stale ? "状态可能已过期" : presentation.label;
  const color = stale ? "orange" : presentation.color;
  const tooltip = stale
    ? `服务端状态为 ${value}，但${staleHint ?? "数据可能已过期"}；请刷新后再操作。`
    : presentation.known
      ? `状态码：${value}（以服务端返回为准）`
      : `未识别的状态码：${value}，已按原始值展示`;

  return (
    <Tooltip title={tooltip}>
      <Tag color={color}>{label}</Tag>
    </Tooltip>
  );
}
