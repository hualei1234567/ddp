import { PlusOutlined } from "@ant-design/icons";
import { Button, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useState } from "react";

import DeviceActionBar from "../../components/devices/DeviceActionBar";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import SensorFormModal from "../../components/devices/SensorFormModal";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useRefreshOnToken } from "../../hooks/useRefreshOnToken";
import {
  devicesApi,
  type DeviceAction,
  type DeviceActionPayload,
  type SensorRecord,
} from "../../services/devices";
import {
  formatDateTime,
  formatMetric,
  formatResolution,
  formatText,
} from "../../services/format";
import { getSensorType } from "../../services/devicePresentation";

interface SensorListProps {
  projectId: string;
  deviceId: string;
  /** 详情页刷新令牌；变化时重新从第一页加载。 */
  refreshToken?: number;
}

function buildColumns(options: {
  busySensorId: string | null;
  onEdit: (sensor: SensorRecord) => void;
  onAction: (
    sensor: SensorRecord,
    action: DeviceAction,
    payload: DeviceActionPayload,
  ) => Promise<void>;
}): TableColumnsType<SensorRecord> {
  return [
  {
    title: "传感器 ID",
    dataIndex: "sensor_id",
    width: 210,
    fixed: "left",
    render: (value: string) => (
      <IdentifierValue value={value} label="传感器 ID" />
    ),
  },
  {
    title: "编码",
    dataIndex: "code",
    width: 140,
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
    title: "标定引用",
    key: "calibration_refs",
    width: 230,
    render: (_value, record) => (
      <Space direction="vertical" size={2}>
        <Space size={4} align="center">
          <Typography.Text type="secondary">内参：</Typography.Text>
          <IdentifierValue
            value={record.intrinsic_calibration_id}
            label="内参标定 ID"
          />
        </Space>
        <Space size={4} align="center">
          <Typography.Text type="secondary">外参：</Typography.Text>
          <IdentifierValue
            value={record.extrinsic_calibration_id}
            label="外参标定 ID"
          />
        </Space>
      </Space>
    ),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 120,
    render: (value: string) => <StatusTag kind="sensor" value={value} />,
  },
  {
    title: "更新时间",
    dataIndex: "updated_at",
    render: (value: string | null) => formatDateTime(value),
  },
  {
    title: "操作",
    key: "actions",
    width: 300,
    render: (_value, record) => (
      <Space direction="vertical" size={6}>
        <Button size="small" onClick={() => options.onEdit(record)}>
          编辑
        </Button>
        <DeviceActionBar
          allowedActions={record.allowed_actions}
          entityLabel="传感器"
          compact
          busy={options.busySensorId === record.sensor_id}
          onAction={(action, payload) =>
            options.onAction(record, action, payload)
          }
        />
      </Space>
    ),
  },
];
}
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

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<SensorRecord | null>(null);
  const [busySensorId, setBusySensorId] = useState<string | null>(null);

  useRefreshOnToken(refreshToken, reload);

  const handleAction = useCallback(
    async (
      sensor: SensorRecord,
      action: DeviceAction,
      payload: DeviceActionPayload,
    ) => {
      setBusySensorId(sensor.sensor_id);
      try {
        await devicesApi.actOnSensor(projectId, sensor.sensor_id, action, payload, {
          version: sensor.version,
        });
        reload();
      } finally {
        setBusySensorId(null);
      }
    },
    [projectId, reload],
  );

  const columns = buildColumns({
    busySensorId,
    onEdit: (sensor) => setEditing(sensor),
    onAction: handleAction,
  });

  const createButton = (
    <Button
      type="primary"
      size="small"
      icon={<PlusOutlined />}
      onClick={() => setCreateOpen(true)}
    >
      新建传感器
    </Button>
  );

  const formModals = (
    <>
      <SensorFormModal
        projectId={projectId}
        deviceId={deviceId}
        mode="create"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={() => reload()}
      />
      <SensorFormModal
        projectId={projectId}
        deviceId={deviceId}
        mode="edit"
        sensor={editing}
        open={editing !== null}
        onClose={() => setEditing(null)}
        onSaved={() => reload()}
      />
    </>
  );

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
    return (
      <Space direction="vertical" size={12} className="ddp-stack">
        <ApiErrorState error={error} onRetry={reload} compact />
        {createButton}
        {formModals}
      </Space>
    );
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
              : "该设备还没有关联传感器记录。可点击「新建传感器」登记编码、类型、坐标系与采样率。"
          }
          action={createButton}
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
        {formModals}
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? <PageState variant="stale" compact onRetry={reload} /> : null}
      <Space wrap>
        <Typography.Text type="secondary">
          已加载 {items.length} 条传感器
        </Typography.Text>
        {createButton}
      </Space>
      <Table<SensorRecord>
        rowKey="sensor_id"
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1480 }}
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
      {formModals}
    </Space>
  );
}
