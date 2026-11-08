import { Alert, Button, Card, Descriptions, Space, Tooltip } from "antd";

import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";

interface SopPanelProps {
  sopId: string | null;
  sopVersion: string | null;
  /** 工作台侧栏可使用紧凑形态。 */
  compact?: boolean;
}

/**
 * SOP 面板。
 *
 * DDP-301 草案只定义任务的 `sop_id` / `sop_version` 快照，尚未定义 SOP 正文
 * 查询端点。这里如实展示快照，并把正文查看标为 Disabled，避免用本地文案冒充
 * 已发布 SOP 内容。
 */
export default function SopPanel({
  sopId,
  sopVersion,
  compact = false,
}: SopPanelProps) {
  if (!sopId && !sopVersion) {
    return (
      <PageState
        variant="empty"
        compact={compact}
        title="任务未关联 SOP 快照"
        description="任务响应中没有 sop_id / sop_version；现场不得自行使用未记录版本的 SOP。"
      />
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <Alert
        type="warning"
        showIcon
        message="SOP 正文接口尚未在阶段 2 契约草案中定义"
        description="当前只展示任务执行时记录的 SOP 版本快照。正文查看与版本变更确认需等待契约冻结；在此之前不得以本地文案替代正式 SOP。"
      />
      <Card size="small" title="执行版本快照">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="SOP ID">
            <IdentifierValue value={sopId} label="SOP ID" />
          </Descriptions.Item>
          <Descriptions.Item label="SOP 版本">
            {sopVersion ?? "—"}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Tooltip title="SOP 正文查询端点未在 DDP-301 草案中定义">
        <span>
          <Button disabled>全屏查看 SOP</Button>
        </span>
      </Tooltip>
    </Space>
  );
}
