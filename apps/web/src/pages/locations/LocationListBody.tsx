import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Select, Space, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import StatusTag from "../../components/ui/StatusTag";
import type { ApiError } from "../../services/api";
import { formatDateTime, formatText } from "../../services/format";
import {
  locationKindOptions,
  presentLocationKind,
} from "../../services/locationPresentation";
import { locationDetailPath } from "../../services/locationUrl";
import type { PagePhase } from "../../services/pagePhase";
import type { Location, LocationKind } from "../../types/location";

export interface LocationListBodyProps {
  kind: LocationKind | undefined;
  phase: PagePhase;
  items: Location[];
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: ApiError | null;
  loadMoreError: ApiError | null;
  stale: boolean;
  onKindChange: (value: LocationKind | undefined) => void;
  onReload: () => void;
  onLoadMore: () => void;
  onCreate: () => void;
  createModal?: ReactNode;
}

const KIND_OPTIONS = locationKindOptions();

const columns: ColumnsType<Location> = [
  {
    title: "名称 / 编码",
    key: "name",
    render: (_, item) => (
      <Space direction="vertical" size={0}>
        <Link to={locationDetailPath(item.location_id)}>{item.name}</Link>
        <Typography.Text type="secondary">{item.code}</Typography.Text>
      </Space>
    ),
  },
  {
    title: "类型",
    dataIndex: "kind",
    width: 110,
    render: (value: string) => (
      <StatusTag value={value} presentation={presentLocationKind} />
    ),
  },
  {
    title: "地址",
    dataIndex: "address",
    render: (value: string | null) => formatText(value),
  },
  {
    title: "更新时间",
    dataIndex: "updated_at",
    width: 190,
    render: (value: string | null) => formatDateTime(value),
  },
];

/**
 * 场地列表展示层。
 *
 * 刻意没有「删除」列与状态列：后端没有 DELETE，也没有 status——
 * 场地被设备与采集任务以复合外键引用，页面不画一个注定失败的按钮。
 */
export default function LocationListBody({
  kind,
  phase,
  items,
  loading,
  refreshing,
  loadingMore,
  hasMore,
  error,
  loadMoreError,
  stale,
  onKindChange,
  onReload,
  onLoadMore,
  onCreate,
  createModal,
}: LocationListBodyProps) {
  const forbidden = phase === "forbidden";

  return (
    <PageContainer
      title="场地管理"
      description="基地 / 房间 / 实验室的组织维度，被设备与采集任务引用；不提供删除，也没有启用/停用状态。"
      filters={
        <Space wrap>
          <Typography.Text>类型</Typography.Text>
          <Select
            allowClear
            value={kind}
            placeholder="全部类型"
            style={{ minWidth: 150 }}
            options={KIND_OPTIONS}
            onChange={(value: LocationKind | undefined) => onKindChange(value)}
          />
        </Space>
      }
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            disabled={forbidden}
            onClick={onCreate}
          >
            新建场地
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={onReload}>
            刷新
          </Button>
        </Space>
      }
    >
      {stale ? (
        <Typography.Text type="warning">
          最近一次刷新失败，展示的是上次成功获取的数据。
        </Typography.Text>
      ) : null}
      <Table<Location>
        rowKey="location_id"
        size="small"
        loading={loading}
        columns={forbidden ? [] : columns}
        dataSource={forbidden ? [] : items}
        pagination={false}
        locale={{ emptyText: error ? "加载失败" : "暂无场地" }}
      />
      {error ? <ApiErrorState error={error} onRetry={onReload} /> : null}
      {hasMore ? (
        <Button
          block
          style={{ marginTop: 12 }}
          loading={loadingMore}
          disabled={forbidden}
          onClick={onLoadMore}
        >
          加载更多
        </Button>
      ) : null}
      {loadMoreError ? (
        <PageState
          compact
          variant="partial"
          description={`加载更多失败：${loadMoreError.message}；已展示的数据仍然有效。`}
          onRetry={onLoadMore}
        />
      ) : null}
      {createModal}
    </PageContainer>
  );
}
