import { Tag, Tooltip } from "antd";

import {
  getStatusPresentation,
  type EquipmentStatusKind,
} from "../../services/devicePresentation";

interface StatusTagProps {
  kind: EquipmentStatusKind;
  value: string;
}

/**
 * 状态标签只展示服务端返回值；未识别状态保留原始码并给出提示，
 * 不使用状态值决定按钮可用性或权限。
 */
export default function StatusTag({ kind, value }: StatusTagProps) {
  const presentation = getStatusPresentation(kind, value);
  const tooltip = presentation.known
    ? `状态码：${value}（状态以服务端返回为准）`
    : `未识别的状态码：${value}，已按原始值展示`;

  return (
    <Tooltip title={tooltip}>
      <Tag color={presentation.color}>{presentation.label}</Tag>
    </Tooltip>
  );
}
