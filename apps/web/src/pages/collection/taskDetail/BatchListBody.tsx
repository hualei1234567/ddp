import {
  Card,
  Descriptions,
  Space,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";

import CollectionStatusTag from "../../../components/collection/CollectionStatusTag";
import LoadMoreBar from "../../../components/devices/LoadMoreBar";
import ApiErrorState from "../../../components/ui/ApiErrorState";
import IdentifierValue from "../../../components/ui/IdentifierValue";
import PageState from "../../../components/ui/PageState";
import type { ApiError } from "../../../services/api";
import { formatDateTime } from "../../../services/format";
import type { CollectionBatch } from "../../../types/collection";

function batchColumns(): TableColumnsType<CollectionBatch> {
  return [
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
  onRetry,
  onLoadMore,
}: BatchListBodyProps) {
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
    return <ApiErrorState error={error} onRetry={onRetry} compact />;
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
      {items.length === 0 ? (
        <PageState
          variant="empty"
          compact
          title="暂无批次"
          description="任务尚未拆分批次；批次创建接口未在 DDP-305 前端范围内开放。"
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
              </Descriptions>
            </Card>
          ))}
        </Space>
      ) : (
        <Table<CollectionBatch>
          rowKey="batch_id"
          columns={batchColumns()}
          dataSource={items}
          pagination={false}
          scroll={{ x: 1200 }}
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
    </Space>
  );
}
