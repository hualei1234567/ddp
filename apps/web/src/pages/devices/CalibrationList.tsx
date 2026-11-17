import { PlusOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import CalibrationActionBar from "../../components/devices/CalibrationActionBar";
import CalibrationCreateModal from "../../components/devices/CalibrationCreateModal";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useRefreshOnToken } from "../../hooks/useRefreshOnToken";
import { devicesApi } from "../../services/devices";
import type {
  CalibrationAction,
  CalibrationRecordWithActions,
} from "../../services/devices";
import {
  isCalibrationExpired,
  summarizeCalibrationExpiry,
} from "../../services/deviceFreshness";
import type { CalibrationExpirySummary } from "../../services/deviceFreshness";
import {
  formatCapabilityProfile,
  formatDateTime,
  formatText,
} from "../../services/format";
import type { CalibrationRecord } from "../../types/device";

interface CalibrationListProps {
  projectId: string;
  deviceId: string;
  /** 设备已绑定的标定配置；用于新建标定时预填。 */
  calibrationProfileId?: string | null;
  /** 详情页刷新令牌；变化时重新从第一页加载。 */
  refreshToken?: number;
  /** 详情页概览需要标定过期摘要，不必等用户打开 Tab。 */
  onExpiryChange?: (summary: CalibrationExpirySummary) => void;
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

function buildColumns(options: {
  busyCalibrationId: string | null;
  onAction: (
    record: CalibrationRecordWithActions,
    action: CalibrationAction,
  ) => Promise<void>;
}): TableColumnsType<CalibrationRecordWithActions> {
  return [
  {
    title: "标定 ID",
    dataIndex: "calibration_id",
    width: 210,
    fixed: "left",
    render: (value: string) => (
      <IdentifierValue value={value} label="标定 ID" />
    ),
  },
  {
    title: "标定类型",
    dataIndex: "calibration_type",
    width: 140,
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
    width: 150,
    render: (value: string, record) => {
      const expired = isCalibrationExpired(record);
      return (
        <Space size={4} wrap>
          <StatusTag kind="calibration" value={value} />
          {expired && value.trim().toUpperCase() !== "EXPIRED" ? (
            <Tag color="orange">已过期</Tag>
          ) : null}
        </Space>
      );
    },
  },
  {
    title: "有效期",
    key: "validity",
    width: 220,
    render: (_value, record) => {
      const expired = isCalibrationExpired(record);
      return (
        <Typography.Text type={expired ? "danger" : undefined}>
          {formatValidity(record.valid_from, record.valid_to)}
        </Typography.Text>
      );
    },
  },
  {
    title: "执行人 / 校验人",
    key: "people",
    width: 220,
    render: (_value, record) => (
      <Space direction="vertical" size={2}>
        <Space size={4} align="center">
          <Typography.Text type="secondary">执行：</Typography.Text>
          <IdentifierValue value={record.performed_by} label="执行人 ID" />
        </Space>
        <Space size={4} align="center">
          <Typography.Text type="secondary">校验：</Typography.Text>
          <IdentifierValue value={record.validated_by} label="校验人 ID" />
        </Space>
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
    width: 80,
    render: (value: string[]) => `${value.length} 项`,
  },
  {
    title: "版本",
    dataIndex: "version",
    width: 70,
    render: (value: number) => `v${value}`,
  },
  {
    title: "更新时间",
    dataIndex: "updated_at",
    width: 170,
    render: (value: string | null) => formatDateTime(value),
  },
  {
    title: "操作",
    key: "actions",
    width: 300,
    fixed: "right",
    render: (_value, record) => (
      <CalibrationActionBar
        allowedActions={record.allowed_actions}
        busy={options.busyCalibrationId === record.calibration_id}
        compact
        onAction={(action) => options.onAction(record, action)}
      />
    ),
  },
];
}
export interface CalibrationFreshnessNoticeProps {
  records: CalibrationRecord[];
  /** 最近一次刷新失败但保留了旧数据。 */
  stale: boolean;
  onRetry: () => void;
  now?: number;
}

/**
 * 标定过期提示：有效期来自记录自身 `valid_to` 或 EXPIRED 状态，
 * 不使用前端硬编码阈值；刷新失败时与过期提示合并展示。
 */
export function CalibrationFreshnessNotice({
  records,
  stale,
  onRetry,
  now = Date.now(),
}: CalibrationFreshnessNoticeProps) {
  const expiry = summarizeCalibrationExpiry(records, now);
  const hasExpired = expiry.expiredCount > 0;

  if (!stale && !hasExpired) {
    return null;
  }

  const title =
    hasExpired && stale
      ? "标定可能过期，且最近刷新失败"
      : hasExpired
        ? "存在已过期标定"
        : undefined;
  const description = [
    hasExpired
      ? `已加载范围中有 ${expiry.expiredCount} 条标定已过期${
          expiry.latestExpiredValidTo
            ? `（最近有效期截止：${formatDateTime(expiry.latestExpiredValidTo)}）`
            : ""
        }，请重新标定或刷新后再使用。`
      : null,
    stale ? "当前展示的是上次成功获取的数据。" : null,
  ]
    .filter((text): text is string => Boolean(text))
    .join(" ");

  return (
    <Space direction="vertical" size={8} className="ddp-stack">
      <PageState
        variant="stale"
        compact
        title={title}
        description={description}
        onRetry={onRetry}
      />
      {hasExpired ? (
        <Tag color="orange">已过期 {expiry.expiredCount} 条</Tag>
      ) : null}
    </Space>
  );
}

export default function CalibrationList({
  projectId,
  deviceId,
  calibrationProfileId = null,
  refreshToken = 0,
  onExpiryChange,
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

  const [createOpen, setCreateOpen] = useState(false);
  const [busyCalibrationId, setBusyCalibrationId] = useState<string | null>(
    null,
  );

  useRefreshOnToken(refreshToken, reload);

  const handleAction = useCallback(
    async (record: CalibrationRecordWithActions, action: CalibrationAction) => {
      setBusyCalibrationId(record.calibration_id);
      try {
        await devicesApi.actOnCalibration(
          projectId,
          record.calibration_id,
          action,
          { version: record.version },
        );
        reload();
      } finally {
        setBusyCalibrationId(null);
      }
    },
    [projectId, reload],
  );

  const columns = buildColumns({ busyCalibrationId, onAction: handleAction });

  const createButton = (
    <Button
      type="primary"
      size="small"
      icon={<PlusOutlined />}
      onClick={() => setCreateOpen(true)}
    >
      登记标定
    </Button>
  );

  const createModal = (
    <CalibrationCreateModal
      projectId={projectId}
      deviceId={deviceId}
      calibrationProfileId={calibrationProfileId}
      open={createOpen}
      onClose={() => setCreateOpen(false)}
      onSaved={reload}
    />
  );

  // 数据变化时重新取当前时间，摘要与提示共用同一时间基准。
  const { evaluatedAt, expiry } = useMemo(() => {
    const now = Date.now();
    return { evaluatedAt: now, expiry: summarizeCalibrationExpiry(items, now) };
  }, [items]);

  useEffect(() => {
    onExpiryChange?.(expiry);
  }, [expiry, onExpiryChange]);

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
    return (
      <Space direction="vertical" size={12} className="ddp-stack">
        <ApiErrorState error={error} onRetry={reload} compact />
        {createButton}
        {createModal}
      </Space>
    );
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
              : "该设备还没有标定记录。可点击「登记标定」创建草稿；提交与校验动作在登记后执行。"
          }
          action={createButton}
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
        {createModal}
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      <CalibrationFreshnessNotice
        records={items}
        stale={stale}
        onRetry={reload}
        now={evaluatedAt}
      />
      <Space wrap>
        <Typography.Text type="secondary">
          已加载 {items.length} 条标定记录
        </Typography.Text>
        {createButton}
      </Space>
      <Table<CalibrationRecordWithActions>
        rowKey="calibration_id"
        columns={columns}
        dataSource={items}
        pagination={false}
        scroll={{ x: 1540 }}
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
      {createModal}
    </Space>
  );
}
