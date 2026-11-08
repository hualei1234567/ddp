import { Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback } from "react";

import LoadMoreBar from "../../components/devices/LoadMoreBar";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useRefreshOnToken } from "../../hooks/useRefreshOnToken";
import { devicesApi } from "../../services/devices";
import {
  formatDateTime,
  formatMetric,
  formatResolution,
  formatText,
} from "../../services/format";
import { getSensorType } from "../../services/devicePresentation";
import type { Sensor } from "../../types/device";

interface SensorListProps {
  projectId: string;
  deviceId: string;
  /** 详情页刷新令牌；变化时重新从第一页加载。 */
  refreshToken?: number;
}

const columns: TableColumnsType<Sensor> = [
  {
    title: "编码",
    dataIndex: "code",
    render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
  },
  {
    title: "类型",
    dataIndex: "sensor_type",
    render: (value: string) => getSensorType(value).label,
  },
  {
    title: "厂商 / 型号",
    key: "vendor_model",
    render: (_value, record) =>
      formatText(
        [record.vendor, record.model]
          .filter((item): item is string => Boolean(item))
          .join(" / "),
      ),
  },
  {
    title: "序列号",
    dataIndex: "serial_number",
    render: (value: string | null) => formatText(value),
  },
  {
    title: "采样率",
    dataIndex: "sample_rate_hz",
    render: (value: number | null) => formatMetric(value, "Hz"),
  },
  {
    title: "分辨率",
    key: "resolution",
    render: (_value, record) =>
      formatResolution(record.resolution_w, record.resolution_h),
  },
  {
    title: "同步",
    key: "sync",
    render: (_value, record) =>
      formatText(
        [record.sync_mode, record.sync_group]
          .filter((item): item is string => Boolean(item))
          .join(" / "),
      ),
  },
  {
    title: "状态",
    dataIndex: "status",
    render: (value: string) => <StatusTag kind="sensor" value={value} />,
  },
  {
    title: "更新时间",
    dataIndex: "updated_at",
    render: (value: string | null) => formatDateTime(value),
  },
];

export default function SensorList({
  projectId,
  deviceId,
  refreshToken = 0,
}: SensorListProps) {
  const loadSensors = useCallback(
    (options: CursorPageRequestOptions) =>
      devicesApi.listSensors(projectId, deviceId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, deviceId],
  );
  const {
    items,
    hasMore,
    loading,
    refreshing,
    loadingMore,
    error,
    loadMoreError,
    stale,
    reload,
    loadMore,
  } = useCursorPage(loadSensors, (sensor) => sensor.sensor_id);

  useRefreshOnToken(refreshToken, reload);

  if (items.length === 0 && loading) {
    return (
      <PageState
        variant="loading"
        compact
        title="正在加载传感器"
        description="正在读取该设备关联的传感器档案。"
      />
    );
  }

  if (items.length === 0 && error) {
    return <ApiErrorState error={error} onRetry={reload} compact />;
  }

  if (items.length === 0) {
    return (
      <Space direction="vertical" size={12} className="ddp-stack">
        <PageState
          variant="empty"
          compact
          title="暂无传感器"
          description={
            hasMore
              ? "已加载范围内没有传感器记录，可继续加载更多。"
              : "该设备还没有关联传感器记录；传感器写入流程接入后会在此显示。"
          }
        />
        <LoadMoreBar
          loadedCount={items.length}
          unit="条传感器记录"
          hasMore={hasMore}
          loadingMore={loadingMore}
          disabled={loading || refreshing}
          error={loadMoreError}
          onLoadMore={loadMore}
        />
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? <PageState variant="stale" compact onRetry={reload} /> : null}
      <Table<Sensor>
        rowKey="sensor_id"
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 900 }}
      />
      <LoadMoreBar
        loadedCount={items.length}
        unit="条传感器记录"
        hasMore={hasMore}
        loadingMore={loadingMore}
        disabled={loading}
        error={loadMoreError}
        onLoadMore={loadMore}
      />
    </Space>
  );
}
