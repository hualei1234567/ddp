import { PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Space,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";

import BatchActionBar from "../../../components/collection/BatchActionBar";
import CollectionStatusTag from "../../../components/collection/CollectionStatusTag";
import LoadMoreBar from "../../../components/devices/LoadMoreBar";
import ApiErrorState from "../../../components/ui/ApiErrorState";
import IdentifierValue from "../../../components/ui/IdentifierValue";
import PageState from "../../../components/ui/PageState";
import type { ApiError } from "../../../services/api";
import { formatDateTime } from "../../../services/format";
import type { CollectionBatch } from "../../../types/collection";

function batchColumns(
  busyBatchId: string | null,
  onAction: BatchListBodyProps["onAction"],
): TableColumnsType<CollectionBatch> {
  const columns: TableColumnsType<CollectionBatch> = [
    {
      title: "批次",
      key: "batch",
      width: 150,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>#{record.sequence_no}</Typography.Text>
          <Typography.Text type="secondary">{record.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "批次 ID",
      dataIndex: "batch_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue value={value} label="批次 ID" />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => (
        <CollectionStatusTag kind="batch" value={value} />
      ),
    },
    {
      title: "执行人",
      dataIndex: "assigned_to",
      width: 180,
      render: (value: string | null) => (
        <IdentifierValue value={value} label="执行人 ID" />
      ),
    },
    {
      title: "计划时间",
      key: "schedule",
      width: 190,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>{formatDateTime(record.scheduled_start)}</span>
          <Typography.Text type="secondary">
            至 {formatDateTime(record.scheduled_end)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "数量",
      key: "counts",
      width: 190,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>预计 {record.expected_count}</span>
          <Typography.Text type="secondary">
            已提交 {record.submitted_count}；已验收 {record.accepted_count}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      render: (value: string) => formatDateTime(value),
    },
  ];

  if (onAction) {
    columns.push({
      title: "操作",
      key: "actions",
      width: 260,
      fixed: "right",
      render: (_value, record) => (
        <BatchActionBar
          allowedActions={record.allowed_actions}
          busy={busyBatchId === record.batch_id}
          compact
          onAction={(action, reason) => onAction(record, action, reason)}
        />
      ),
    });
  }

  return columns;
}

export interface BatchListBodyProps {
  items: CollectionBatch[];
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  stale: boolean;
  isMobile: boolean;
  busyBatchId?: string | null;
  /** 批次动作；非法迁移由服务端拒绝，组件只展示错误。 */
  onAction?: (
    batch: CollectionBatch,
    action: string,
    reason: string | null,
  ) => Promise<void>;
  onCreate?: () => void;
  /** 创建弹窗由页面注入，列表本体保持可静态渲染与测试。 */
  createModal?: ReactNode;
  onRetry: () => void;
  onLoadMore: () => void;
}

/** 批次列表主体；与请求解耦，便于覆盖 Loading/Empty/Error/Stale/Partial。 */
export default function BatchListBody({
  items,
  loading,
  refreshing,
  loadingMore,
  hasMore,
  error,
  loadMoreError,
  stale,
  isMobile,
  busyBatchId = null,
  onAction,
  onCreate,
  createModal,
  onRetry,
  onLoadMore,
}: BatchListBodyProps) {
  const createButton = onCreate ? (
    <Button type="primary" size="small" icon={<PlusOutlined />} onClick={onCreate}>
      新建批次
    </Button>
  ) : null;

  if (loading) {
    return (
      <PageState
        variant="loading"
        compact
        title="正在加载批次"
        description="正在读取任务的批次与执行进度。"
      />
    );
  }
  if (error && items.length === 0) {
    return (
      <Space direction="vertical" size={12} className="ddp-stack">
        <ApiErrorState error={error} onRetry={onRetry} compact />
        {createButton}
        {createModal}
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的批次数据。"
          onRetry={onRetry}
        />
      ) : null}
      <Space wrap>
        <Typography.Text type="secondary">
          已加载 {items.length} 个批次
        </Typography.Text>
        {createButton}
      </Space>
      {items.length === 0 ? (
        <PageState
          variant="empty"
          compact
          title="暂无批次"
          description="任务尚未拆分批次。可创建批次后按服务端 allowed_actions 执行开始、暂停或提交。"
          action={
            onCreate ? (
              <Button type="primary" onClick={onCreate}>
                新建批次
              </Button>
            ) : undefined
          }
        />
      ) : isMobile ? (
        <Space direction="vertical" size={12} className="ddp-stack">
          {items.map((batch) => (
            <Card
              key={batch.batch_id}
              size="small"
              title={
                <Space wrap>
                  <span>
                    #{batch.sequence_no} {batch.code}
                  </span>
                  <CollectionStatusTag kind="batch" value={batch.status} />
                </Space>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="批次 ID">
                  <IdentifierValue value={batch.batch_id} label="批次 ID" />
                </Descriptions.Item>
                <Descriptions.Item label="执行人">
                  <IdentifierValue value={batch.assigned_to} label="执行人 ID" />
                </Descriptions.Item>
                <Descriptions.Item label="计划时间">
                  {formatDateTime(batch.scheduled_start)} 至{" "}
                  {formatDateTime(batch.scheduled_end)}
                </Descriptions.Item>
                <Descriptions.Item label="数量">
                  预计 {batch.expected_count}；已提交 {batch.submitted_count}；
                  已验收 {batch.accepted_count}
                </Descriptions.Item>
                {onAction ? (
                  <Descriptions.Item label="动作">
                    <BatchActionBar
                      allowedActions={batch.allowed_actions}
                      busy={busyBatchId === batch.batch_id}
                      onAction={(action, reason) =>
                        onAction(batch, action, reason)
                      }
                    />
                  </Descriptions.Item>
                ) : null}
              </Descriptions>
            </Card>
          ))}
        </Space>
      ) : (
        <Table<CollectionBatch>
          rowKey="batch_id"
          columns={batchColumns(busyBatchId, onAction)}
          dataSource={items}
          pagination={false}
          scroll={{ x: 1460 }}
        />
      )}
      <LoadMoreBar
        loadedCount={items.length}
        unit="个批次"
        hasMore={hasMore}
        loadingMore={loadingMore}
        disabled={loading || refreshing}
        error={loadMoreError}
        onLoadMore={onLoadMore}
        block={isMobile}
      />
      {createModal}
    </Space>
  );
}
