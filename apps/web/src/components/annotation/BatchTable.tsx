import { Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { WithAllowedActions } from "../../services/annotationAllowedActions";
import { resolveAnnotationActions } from "../../services/annotationActions";
import type { ApiError } from "../../services/api";
import { formatDateTime } from "../../services/format";
import type { AnnotationBatch } from "../../types/annotation";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import ActionButtons from "./ActionButtons";
import type { AnnotationActionPayload } from "./ActionButtons";
import AnnotationStatusTag from "./AnnotationStatusTag";

function columns(
  busyAction: string | null,
  onAction?: (
    batch: WithAllowedActions<AnnotationBatch>,
    action: string,
    payload: AnnotationActionPayload,
  ) => Promise<void>,
): TableColumnsType<WithAllowedActions<AnnotationBatch>> {
  return [
    {
      title: "批次",
      key: "batch",
      width: 180,
      render: (_value, batch) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            #{batch.sequence_no} {batch.code}
          </Typography.Text>
          <Typography.Text type="secondary">
            {batch.name ?? batch.stratum ?? "—"}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "批次 ID",
      dataIndex: "batch_id",
      width: 210,
      render: (value: string) => <IdentifierValue value={value} label="批次 ID" />,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "计划 / 已提交",
      key: "counts",
      width: 160,
      render: (_value, batch) => (
        <span>
          {batch.planned_item_count} / {batch.submitted_item_count}
        </span>
      ),
    },
    {
      title: "通过 / 驳回 / 返工",
      key: "quality",
      width: 180,
      render: (_value, batch) => (
        <Typography.Text type="secondary">
          {batch.approved_item_count} / {batch.rejected_item_count} /{" "}
          {batch.rework_count}
        </Typography.Text>
      ),
    },
    {
      title: "指派人员",
      key: "assignees",
      width: 220,
      render: (_value, batch) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>
            标注 {batch.assignee_ids.length} 人
          </Typography.Text>
          <Typography.Text type="secondary">
            复核池 {batch.reviewer_pool_ids.length} 人
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "截止时间",
      dataIndex: "due_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "动作",
      key: "actions",
      width: 320,
      fixed: "right",
      render: (_value, batch) => {
        if (!onAction) {
          return <Typography.Text type="secondary">—</Typography.Text>;
        }
        const resolution = resolveAnnotationActions("batch", batch.status, batch.allowed_actions);
        return (
          <ActionButtons
            kind="batch"
            size="small"
            actions={resolution.actions}
            busyAction={
              busyAction?.startsWith(`${batch.batch_id}:`)
                ? busyAction.split(":")[1] ?? null
                : null
            }
            onAction={(action, payload) => onAction(batch, action, payload)}
          />
        );
      },
    },
  ];
}

interface BatchTableProps {
  items: WithAllowedActions<AnnotationBatch>[];
  loading: boolean;
  error: ApiError | null;
  stale: boolean;
  onRetry: () => void;
  /** `${batch_id}:${action}`，用于只让当前行按钮转圈。 */
  busyAction?: string | null;
  onAction?: (
    batch: WithAllowedActions<AnnotationBatch>,
    action: string,
    payload: AnnotationActionPayload,
  ) => Promise<void>;
}

export default function BatchTable({
  items,
  loading,
  error,
  stale,
  onRetry,
  busyAction = null,
  onAction,
}: BatchTableProps) {
  if (loading) {
    return <PageState compact variant="loading" title="正在加载批次" />;
  }
  if (error && items.length === 0) {
    return <ApiErrorState error={error} onRetry={onRetry} compact />;
  }
  if (items.length === 0) {
    return (
      <PageState
        compact
        variant="empty"
        title="暂无批次"
        description="任务尚未拆分批次。"
      />
    );
  }
  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="批次刷新失败，展示上次成功获取的数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Table<WithAllowedActions<AnnotationBatch>>
        rowKey="batch_id"
        size="small"
        columns={columns(busyAction, onAction)}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1520 }}
      />
    </Space>
  );
}
