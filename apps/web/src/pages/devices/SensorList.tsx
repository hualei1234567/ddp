import { Alert, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback } from "react";

import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageState from "../../components/ui/PageState";
import { useRequest } from "../../hooks/useRequest";
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

export default function SensorList({ projectId, deviceId }: SensorListProps) {
  const loadSensors = useCallback(
    (signal: AbortSignal) =>
      devicesApi.listSensors(projectId, deviceId, { signal }),
    [projectId, deviceId],
  );
  const { data, error, loading, stale, reload } = useRequest(loadSensors);

  if (!data && loading) {
    return (
      <PageState
        variant="loading"
        compact
        title="正在加载传感器"
        description="正在读取该设备关联的传感器档案。"
      />
    );
  }

  if (!data && error) {
    return <ApiErrorState error={error} onRetry={reload} compact />;
  }

  if (!data || data.items.length === 0) {
    return (
      <PageState
        variant="empty"
        compact
        title="暂无传感器"
        description="该设备还没有关联传感器记录；传感器写入流程接入后会在此显示。"
      />
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? <PageState variant="stale" compact onRetry={reload} /> : null}
      {data.next_cursor ? (
        <Alert
          type="info"
          showIcon
          message="已加载部分传感器"
          description="服务端返回了 next_cursor；分页加载将在后续任务接入。"
        />
      ) : null}
      <Table<Sensor>
        rowKey="sensor_id"
        columns={columns}
        dataSource={data.items}
        pagination={false}
        scroll={{ x: 900 }}
      />
    </Space>
  );
}
