import { Tag, Tooltip } from "antd";

import {
  getStatusPresentation,
  type EquipmentStatusKind,
} from "../../services/devicePresentation";

interface StatusTagProps {
  kind: EquipmentStatusKind;
  value: string;
  /**
   * 服务端新鲜度判定为过期时，ACTIVE 不得继续显示为“可用”。
   * 这里只改变展示，不改变服务端状态，也不据此决定权限或动作。
   */
  stale?: boolean;
  staleHint?: string;
}

/**
 * 状态标签只展示服务端返回值；未识别状态保留原始码并给出提示，
 * 不使用状态值决定按钮可用性或权限。
 */
export default function StatusTag({
  kind,
  value,
  stale = false,
  staleHint,
}: StatusTagProps) {
  const presentation = getStatusPresentation(kind, value);
  const staleActive = stale && value.trim().toUpperCase() === "ACTIVE";
  const label = staleActive ? "状态可能过期" : presentation.label;
  const color = staleActive ? "orange" : presentation.color;
  const tooltip = staleActive
    ? `服务端状态为 ${value}，但${staleHint ?? "状态已超过服务端新鲜度阈值"}；请刷新或重新检测，不要按“可用”使用。`
    : presentation.known
      ? `状态码：${value}（状态以服务端返回为准）`
      : `未识别的状态码：${value}，已按原始值展示`;

  return (
    <Tooltip title={tooltip}>
      <Tag color={color}>{label}</Tag>
    </Tooltip>
  );
}
