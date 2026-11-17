import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Grid,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import DeviceFormModal from "../../components/devices/DeviceFormModal";
import DeviceFilters from "../../components/devices/DeviceFilters";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import MobileFilterDrawer from "../../components/devices/MobileFilterDrawer";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import type { ApiError } from "../../services/api";
import {
  buildDeviceSearch,
  hasDeviceListFilters,
  readDeviceListFilters,
} from "../../services/deviceListUrl";
import type { DeviceListFilters } from "../../services/deviceListUrl";
import { devicesApi } from "../../services/devices";
import {
  evaluateDeviceFreshness,
  summarizeDeviceFreshness,
} from "../../services/deviceFreshness";
import { resolveDevicePagePhase } from "../../services/devicePageState";
import type { DevicePagePhase } from "../../services/devicePageState";
import {
  formatDateTime,
  formatText,
  formatTimestamp,
} from "../../services/format";
import {
  getDeviceStatus,
  getDeviceType,
} from "../../services/devicePresentation";
import type { Device } from "../../types/device";

export interface DeviceListBodyProps {
  projectId: string;
  filters: DeviceListFilters;
  phase: DevicePagePhase;
  error: ApiError | null;
  items: Device[];
  filteredItems: Device[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  /** 最近一次刷新失败但保留了旧数据。 */
  stale: boolean;
  updatedAt: number | null;
  isMobile: boolean;
  onRetry: () => void;
  onResetFilters: () => void;
  onLoadMore: () => void;
  onOpenDevice: (deviceId: string) => void;
  /** 注入当前时间，便于组件测试与避免单元格重复取时。 */
  now?: number;
}

function buildDetailPath(projectId: string, filters: DeviceListFilters) {
  return {
    to: (deviceId: string) => ({
      pathname: `/devices/${encodeURIComponent(deviceId)}`,
      search: buildDeviceSearch(projectId, filters),
    }),
  };
}

function heartbeatHint(device: Device, now: number): string {
  const freshness = evaluateDeviceFreshness(device, now);
  if (freshness.level !== "stale") {
    return "";
  }
  if (freshness.lastHeartbeatAt) {
    return `最近心跳为 ${formatDateTime(freshness.lastHeartbeatAt)}，已超过服务端下发的新鲜度阈值`;
  }
  return "状态已超过服务端下发的新鲜度阈值";
}

function buildColumns(
  now: number,
  projectId: string,
  filters: DeviceListFilters,
): TableColumnsType<Device> {
  const detail = buildDetailPath(projectId, filters);

  return [
    {
      title: "设备",
      key: "name",
      width: 200,
      fixed: "left",
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.name}</Typography.Text>
          <Typography.Text type="secondary">{record.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "设备 ID",
      dataIndex: "device_id",
      width: 220,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="设备 ID"
          to={detail.to(value)}
        />
      ),
    },
    {
      title: "类型",
      key: "device_type",
      width: 120,
      render: (_value, record) => getDeviceType(record.device_type).label,
    },
    {
      title: "厂商 / 型号",
      key: "vendor_model",
      width: 180,
      render: (_value, record) =>
        formatText(
          [record.vendor, record.model]
            .filter((value): value is string => Boolean(value))
            .join(" / "),
        ),
    },
    {
      title: "序列号",
      dataIndex: "serial_number",
      width: 140,
      render: (value: string | null) => formatText(value),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 150,
      render: (value: string, record) => {
        const freshness = evaluateDeviceFreshness(record, now);
        return (
          <StatusTag
            kind="device"
            value={value}
            stale={freshness.level === "stale"}
            staleHint={heartbeatHint(record, now)}
          />
        );
      },
    },
    {
      title: "位置",
      dataIndex: "location_id",
      width: 190,
      render: (value: string | null) => (
        <IdentifierValue value={value} label="位置 ID" />
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      sorter: (left, right) =>
        (left.updated_at ?? "").localeCompare(right.updated_at ?? ""),
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      fixed: "right",
      render: (_value, record) => (
        <Link
          to={detail.to(record.device_id)}
          onClick={(event) => event.stopPropagation()}
        >
          查看详情
        </Link>
      ),
    },
  ];
}

/**
 * 设备列表主体。与数据请求解耦，便于直接覆盖 Forbidden / Not Found /
 * Loading / Empty / Stale 等展示状态。
 */
export function DeviceListBody({
  projectId,
  filters,
  phase,
  error,
  items,
  filteredItems,
  hasMore,
  loading,
  refreshing,
  loadingMore,
  loadMoreError,
  stale,
  isMobile,
  onRetry,
  onResetFilters,
  onLoadMore,
  onOpenDevice,
  now = Date.now(),
}: DeviceListBodyProps) {
  const filtersActive = hasDeviceListFilters(filters);
  const detail = buildDetailPath(projectId, filters);
  const columns = useMemo(
    () => buildColumns(now, projectId, filters),
    [now, projectId, filters],
  );

  const freshness = summarizeDeviceFreshness(items, now);
  const heartbeatStale = freshness.staleCount > 0;
  const staleTitle = heartbeatStale
    ? "部分设备状态可能已过期"
    : undefined;
  const staleDescription = [
    heartbeatStale
      ? `检测到 ${freshness.staleCount} 台设备心跳超时${
          freshness.latestHeartbeatAt
            ? `（最近心跳 ${formatDateTime(freshness.latestHeartbeatAt)}）`
            : ""
        }；状态可能已过期，请刷新或重新检测。`
      : null,
    stale ? "最近一次刷新失败，当前展示的是上次成功获取的数据。" : null,
  ]
    .filter((text): text is string => Boolean(text))
    .join(" ");

  const scopeText = filtersActive
    ? hasMore
      ? `统计为已加载 ${items.length} 台设备的状态分布；筛选作用于同一范围，还有更多设备未加载。`
      : `统计与筛选覆盖当前项目全部 ${items.length} 台设备。`
    : hasMore
      ? `统计为已加载 ${items.length} 台设备的状态分布；还有更多设备未加载。`
      : `统计覆盖当前项目全部 ${items.length} 台设备。`;

  const summary = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
    }
    return [...counts.entries()];
  }, [items]);

  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;

  let body: ReactNode;
  if (phase === "forbidden" || phase === "not-found") {
    // 权限被拒或资源不可见时整页提示，不展示筛选、统计或缓存数据。
    body = errorBody;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载设备列表"
        description="正在按当前项目加载设备档案。"
      />
    );
  } else if (phase === "error") {
    body = errorBody;
  } else {
    let content: ReactNode;
    if (items.length === 0) {
      content = (
        <PageState
          variant="empty"
          title={filtersActive ? "无匹配结果" : "尚未登记设备"}
          description={
            filtersActive
              ? hasMore
                ? "已加载的设备中没有匹配项；还有更多设备未加载，可继续加载后再筛选。"
                : "当前筛选条件下没有设备，可清空筛选后重试。"
              : hasMore
                ? "已加载范围内没有设备，可继续加载更多。"
                : "当前项目还没有设备记录；设备创建流程接入后会在此显示。"
          }
          action={
            filtersActive ? (
              <Button onClick={onResetFilters}>清空筛选</Button>
            ) : undefined
          }
        />
      );
    } else if (filteredItems.length === 0) {
      content = (
        <PageState
          variant="empty"
          title="无匹配结果"
          description={
            hasMore
              ? "已加载的设备中没有匹配项；还有更多设备未加载，可继续加载后再筛选。"
              : "当前筛选条件下没有设备，可清空筛选后重试。"
          }
          action={<Button onClick={onResetFilters}>清空筛选</Button>}
        />
      );
    } else if (isMobile) {
      content = (
        <Space direction="vertical" size={12} className="ddp-stack">
          {filteredItems.map((device) => {
            const deviceFreshness = evaluateDeviceFreshness(device, now);
            return (
              <Card
                key={device.device_id}
                title={
                  <Space wrap>
                    <span>{device.name}</span>
                    <StatusTag
                      kind="device"
                      value={device.status}
                      stale={deviceFreshness.level === "stale"}
                      staleHint={heartbeatHint(device, now)}
                    />
                  </Space>
                }
                extra={
                  <Link to={detail.to(device.device_id)}>查看详情</Link>
                }
              >
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="编码">
                    {device.code}
                  </Descriptions.Item>
                  <Descriptions.Item label="设备 ID">
                    <IdentifierValue
                      value={device.device_id}
                      label="设备 ID"
                      to={detail.to(device.device_id)}
                    />
                  </Descriptions.Item>
                  <Descriptions.Item label="类型">
                    {getDeviceType(device.device_type).label}
                  </Descriptions.Item>
                  <Descriptions.Item label="厂商 / 型号">
                    {formatText(
                      [device.vendor, device.model]
                        .filter((value): value is string => Boolean(value))
                        .join(" / "),
                    )}
                  </Descriptions.Item>
                  <Descriptions.Item label="序列号">
                    {formatText(device.serial_number)}
                  </Descriptions.Item>
                  <Descriptions.Item label="最近心跳">
                    {formatDateTime(device.last_heartbeat_at ?? null)}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {formatDateTime(device.updated_at)}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            );
          })}
        </Space>
      );
    } else {
      content = (
        <Table<Device>
          rowKey="device_id"
          columns={columns}
          dataSource={filteredItems}
          pagination={false}
          scroll={{ x: 1440 }}
          onRow={(record) => ({
            onClick: () => onOpenDevice(record.device_id),
            style: { cursor: "pointer" },
          })}
        />
      );
    }

    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale || heartbeatStale ? (
          <PageState
            variant="stale"
            compact
            title={staleTitle}
            description={staleDescription}
            onRetry={onRetry}
          />
        ) : null}
        {error && !stale ? (
          <ApiErrorState error={error} onRetry={onRetry} compact />
        ) : null}
        <Space wrap>
          <Tag color="blue">已加载 {items.length} 台</Tag>
          {summary.map(([status, count]) => (
            <Tag key={status}>
              {getDeviceStatus(status).label} {count}
            </Tag>
          ))}
          {heartbeatStale ? (
            <Tag color="orange">心跳超时 {freshness.staleCount} 台</Tag>
          ) : null}
          {freshness.unknownCount > 0 ? (
            <Tag>新鲜度未知 {freshness.unknownCount} 台</Tag>
          ) : null}
        </Space>
        <Typography.Text type="secondary">{scopeText}</Typography.Text>
        {content}
        <LoadMoreBar
          loadedCount={items.length}
          unit="台设备"
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

  return <>{body}</>;
}

function DeviceListContent({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);

  const filters = readDeviceListFilters(searchParams);

  const loadDevices = useCallback(
    (options: CursorPageRequestOptions) =>
      devicesApi.listDevices(projectId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId],
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
    updatedAt,
    reload,
    loadMore,
  } = useCursorPage(loadDevices, (device) => device.device_id);

  const statusOptions = useMemo(() => {
    const values = new Set(items.map((item) => item.status));
    return [...values]
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: getDeviceStatus(value).label }));
  }, [items]);

  const typeOptions = useMemo(() => {
    const values = new Set(items.map((item) => item.device_type));
    return [...values]
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: getDeviceType(value).label }));
  }, [items]);

  const filteredItems = useMemo(() => {
    const search = filters.keyword.toLowerCase();
    return items.filter((item) => {
      if (filters.status && item.status !== filters.status) {
        return false;
      }
      if (filters.deviceType && item.device_type !== filters.deviceType) {
        return false;
      }
      if (search === "") {
        return true;
      }
      return [item.name, item.code, item.serial_number, item.vendor, item.model]
        .filter((value): value is string => typeof value === "string")
        .some((value) => value.toLowerCase().includes(search));
    });
  }, [items, filters.keyword, filters.status, filters.deviceType]);

  const updateFilter = useCallback(
    (key: string, value: string) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (value) {
            next.set(key, value);
          } else {
            next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const resetFilters = useCallback(() => {
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams();
        const projectIdParam = previous.get("project_id");
        if (projectIdParam) {
          next.set("project_id", projectIdParam);
        }
        return next;
      },
      { replace: true },
    );
  }, [setSearchParams]);

  /** 详情链接携带列表筛选，返回列表与刷新后仍可恢复。 */
  const detailTarget = useCallback(
    (deviceId: string) => ({
      pathname: `/devices/${encodeURIComponent(deviceId)}`,
      search: buildDeviceSearch(projectId, filters),
    }),
    [projectId, filters],
  );

  // 首次成功加载（包括空列表）后 updatedAt 才有值，用于区分“未加载”和“已加载但为空”。
  const hasLoaded = updatedAt !== null;
  const phase = resolveDevicePagePhase({
    hasData: hasLoaded,
    loading,
    error,
  });

  const isMobile = screens.md === false;
  const activeFilterCount = [
    filters.keyword,
    filters.status,
    filters.deviceType,
  ].filter((value) => value !== "").length;

  const filterProps = {
    keyword: filters.keyword,
    statusFilter: filters.status,
    typeFilter: filters.deviceType,
    statusOptions,
    typeOptions,
    loadedCount: items.length,
    hasMore,
    disabled: items.length === 0,
    onChange: updateFilter,
    onReset: resetFilters,
  };

  const filtersElement =
    phase === "forbidden" || phase === "not-found" ? undefined : isMobile ? (
      <MobileFilterDrawer {...filterProps} activeCount={activeFilterCount} />
    ) : (
      <DeviceFilters {...filterProps} />
    );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={() => setCreateOpen(true)}
      >
        新建设备
      </Button>
      <Button
        icon={<ReloadOutlined />}
        loading={refreshing}
        onClick={reload}
        title="重新从第一页加载"
      >
        刷新
      </Button>
    </Space>
  );

  return (
    <>
      <PageContainer
        title="设备纳管"
        description="按 DDP-101 设备域契约展示设备档案、状态与标定信息。列表按游标分页加载；筛选与统计基于已加载数据，并随地址栏保留。状态与可用动作以服务端返回为准，前端不做权威权限校验。"
        extra={extra}
        filters={filtersElement}
      >
        <DeviceListBody
          projectId={projectId}
          filters={filters}
          phase={phase}
          error={error}
          items={items}
          filteredItems={filteredItems}
          hasMore={hasMore}
          loading={loading}
          refreshing={refreshing}
          loadingMore={loadingMore}
          loadMoreError={loadMoreError}
          stale={stale}
          updatedAt={updatedAt}
          isMobile={isMobile}
          onRetry={reload}
          onResetFilters={resetFilters}
          onLoadMore={loadMore}
          onOpenDevice={(deviceId) => navigate(detailTarget(deviceId))}
        />
      </PageContainer>
      <DeviceFormModal
        projectId={projectId}
        mode="create"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={(saved) => {
          reload();
          navigate(detailTarget(saved.device_id));
        }}
      />
    </>
  );
}

export default function DeviceListPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();

  if (!projectId) {
    return (
      <PageContainer
        title="设备纳管"
        description="设备数据必须按项目隔离展示。"
      >
        <PageState
          variant="empty"
          title="未选择项目"
          description="请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。前端不会使用硬编码的默认项目。"
          action={
            <Button type="primary" onClick={() => navigate("/")}>
              返回工作台
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return <DeviceListContent key={projectId} projectId={projectId} />;
}
