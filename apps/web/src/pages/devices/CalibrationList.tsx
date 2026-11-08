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
  formatCapabilityProfile,
  formatDateTime,
  formatIdentifier,
  formatText,
} from "../../services/format";
import type { CalibrationRecord } from "../../types/device";

interface CalibrationListProps {
  projectId: string;
  deviceId: string;
  /** 详情页刷新令牌；变化时重新从第一页加载。 */
  refreshToken?: number;
}

function formatValidity(
  validFrom: string | null,
  validTo: string | null,
): string {
  if (!validFrom && !validTo) {
    return "—";
  }
  return `${formatDateTime(validFrom)} 至 ${formatDateTime(validTo)}`;
}

const columns: TableColumnsType<CalibrationRecord> = [
  {
    title: "标定类型",
    dataIndex: "calibration_type",
    render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
  },
  {
    title: "方法",
    dataIndex: "method",
    render: (value: string | null) => formatText(value),
  },
  {
    title: "状态",
    dataIndex: "status",
    render: (value: string) => <StatusTag kind="calibration" value={value} />,
  },
  {
    title: "有效期",
    key: "validity",
    render: (_value, record) =>
      formatValidity(record.valid_from, record.valid_to),
  },
  {
    title: "执行人 / 校验人",
    key: "people",
    render: (_value, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text>
          执行：{formatIdentifier(record.performed_by)}
        </Typography.Text>
        <Typography.Text type="secondary">
          校验：{formatIdentifier(record.validated_by)}
        </Typography.Text>
      </Space>
    ),
  },
  {
    title: "参数键",
    dataIndex: "parameters",
    render: (value: Record<string, unknown> | null) => (
      <Typography.Text ellipsis={{ tooltip: formatCapabilityProfile(value) }}>
        {formatCapabilityProfile(value)}
      </Typography.Text>
    ),
  },
  {
    title: "证据",
    dataIndex: "evidence_asset_ids",
    render: (value: string[]) => `${value.length} 项`,
  },
  {
    title: "版本",
    dataIndex: "version",
    render: (value: number) => `v${value}`,
  },
  {
    title: "更新时间",
    dataIndex: "updated_at",
    render: (value: string | null) => formatDateTime(value),
  },
];

export default function CalibrationList({
  projectId,
  deviceId,
  refreshToken = 0,
}: CalibrationListProps) {
  const loadCalibrations = useCallback(
    (options: CursorPageRequestOptions) =>
      devicesApi.listCalibrations(projectId, deviceId, {
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
  } = useCursorPage(loadCalibrations, (record) => record.calibration_id);

  useRefreshOnToken(refreshToken, reload);

  if (items.length === 0 && loading) {
    return (
      <PageState
        variant="loading"
        compact
        title="正在加载标定记录"
        description="正在读取该设备关联的标定记录。"
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
          title="暂无标定记录"
          description={
            hasMore
              ? "已加载范围内没有标定记录，可继续加载更多。"
              : "该设备还没有标定记录；标定登记流程接入后会在此显示。"
          }
        />
        <LoadMoreBar
          loadedCount={items.length}
          unit="条标定记录"
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
      <Table<CalibrationRecord>
        rowKey="calibration_id"
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1040 }}
      />
      <LoadMoreBar
        loadedCount={items.length}
        unit="条标定记录"
        hasMore={hasMore}
        loadingMore={loadingMore}
        disabled={loading}
        error={loadMoreError}
        onLoadMore={loadMore}
      />
    </Space>
  );
}
