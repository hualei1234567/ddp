import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Grid,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { devicesApi } from "../../services/devices";
import { resolveDevicePagePhase } from "../../services/devicePageState";
import {
  formatDateTime,
  formatIdentifier,
  formatText,
  formatTimestamp,
} from "../../services/format";
import {
  getDeviceStatus,
  getDeviceType,
} from "../../services/devicePresentation";
import { buildProjectQuery } from "../../services/project";
import type { Device } from "../../types/device";

function DeviceFilters({
  keyword,
  statusFilter,
  typeFilter,
  statusOptions,
  typeOptions,
  disabled,
  onChange,
  onReset,
}: {
  keyword: string;
  statusFilter: string;
  typeFilter: string;
  statusOptions: { value: string; label: string }[];
  typeOptions: { value: string; label: string }[];
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onReset: () => void;
}) {
  const [searchText, setSearchText] = useState(keyword);

  useEffect(() => {
    setSearchText(keyword);
  }, [keyword]);

  return (
    <>
      <Input.Search
        allowClear
        value={searchText}
        placeholder="搜索名称、编码、序列号或厂商（当前页）"
        style={{ width: 320, maxWidth: "100%" }}
        onChange={(event) => setSearchText(event.target.value)}
        onSearch={(value) => onChange("q", value.trim())}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="设备状态（当前页）"
        style={{ width: 180 }}
        value={statusFilter || undefined}
        options={statusOptions}
        disabled={disabled}
        onChange={(value) => onChange("status", value ?? "")}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="设备类型（当前页）"
        style={{ width: 180 }}
        value={typeFilter || undefined}
        options={typeOptions}
        disabled={disabled}
        onChange={(value) => onChange("device_type", value ?? "")}
      />
      <Button onClick={onReset}>清空筛选</Button>
      <Typography.Text type="secondary">
        筛选仅作用于已加载数据
      </Typography.Text>
    </>
  );
}

function DeviceListContent({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;
  const [searchParams, setSearchParams] = useSearchParams();

  const keyword = searchParams.get("q") ?? "";
  const statusFilter = searchParams.get("status") ?? "";
  const typeFilter = searchParams.get("device_type") ?? "";
  const projectSearch = buildProjectQuery(projectId);

  const loadDevices = useCallback(
    (signal: AbortSignal) => devicesApi.listDevices(projectId, { signal }),
    [projectId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadDevices);

  const items = useMemo<Device[]>(() => data?.items ?? [], [data]);

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
    const search = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (statusFilter && item.status !== statusFilter) {
        return false;
      }
      if (typeFilter && item.device_type !== typeFilter) {
        return false;
      }
      if (search === "") {
        return true;
      }
      return [item.name, item.code, item.serial_number, item.vendor, item.model]
        .filter((value): value is string => typeof value === "string")
        .some((value) => value.toLowerCase().includes(search));
    });
  }, [items, keyword, statusFilter, typeFilter]);

  const filtersActive =
    keyword.trim() !== "" || statusFilter !== "" || typeFilter !== "";

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

  const detailTarget = (deviceId: string) => ({
    pathname: `/devices/${encodeURIComponent(deviceId)}`,
    search: projectSearch,
  });

  const columns: TableColumnsType<Device> = [
    {
      title: "设备",
      key: "name",
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.name}</Typography.Text>
          <Typography.Text type="secondary">{record.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "类型",
      key: "device_type",
      render: (_value, record) => getDeviceType(record.device_type).label,
    },
    {
      title: "厂商 / 型号",
      key: "vendor_model",
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
      render: (value: string | null) => formatText(value),
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (value: string) => <StatusTag kind="device" value={value} />,
    },
    {
      title: "位置",
      dataIndex: "location_id",
      render: (value: string | null) => (
        <Typography.Text code>{formatIdentifier(value)}</Typography.Text>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      sorter: (left, right) =>
        (left.updated_at ?? "").localeCompare(right.updated_at ?? ""),
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      render: (_value, record) => (
        <Link
          to={detailTarget(record.device_id)}
          onClick={(event) => event.stopPropagation()}
        >
          查看详情
        </Link>
      ),
    },
  ];

  const summary = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
    }
    return [...counts.entries()];
  }, [items]);

  const phase = resolveDevicePagePhase({
    hasData: data !== null,
    loading,
    error,
  });
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={reload} />
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
  } else if (data) {
    let content: ReactNode;
    if (items.length === 0) {
      content = (
        <PageState
          variant="empty"
          title={filtersActive ? "无匹配结果" : "尚未登记设备"}
          description={
            filtersActive
              ? "当前筛选条件下没有设备，可清空筛选后重试。"
              : "当前项目还没有设备记录；设备创建流程接入后会在此显示。"
          }
          action={
            filtersActive ? (
              <Button onClick={resetFilters}>清空筛选</Button>
            ) : undefined
          }
        />
      );
    } else if (filteredItems.length === 0) {
      content = (
        <PageState
          variant="empty"
          title="无匹配结果"
          description="当前筛选条件下没有设备，可清空筛选后重试。"
          action={<Button onClick={resetFilters}>清空筛选</Button>}
        />
      );
    } else if (isMobile) {
      content = (
        <Space direction="vertical" size={12} className="ddp-stack">
          {filteredItems.map((device) => (
            <Card
              key={device.device_id}
              title={
                <Space wrap>
                  <span>{device.name}</span>
                  <StatusTag kind="device" value={device.status} />
                </Space>
              }
              extra={
                <Link to={detailTarget(device.device_id)}>查看详情</Link>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="编码">
                  {device.code}
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
                <Descriptions.Item label="更新时间">
                  {formatDateTime(device.updated_at)}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </Space>
      );
    } else {
      content = (
        <Table<Device>
          rowKey="device_id"
          columns={columns}
          dataSource={filteredItems}
          pagination={false}
          scroll={{ x: 980 }}
          onRow={(record) => ({
            onClick: () => navigate(detailTarget(record.device_id)),
            style: { cursor: "pointer" },
          })}
        />
      );
    }

    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState variant="stale" compact onRetry={reload} />
        ) : null}
        {error && !stale ? (
          <ApiErrorState error={error} onRetry={reload} compact />
        ) : null}
        <Space wrap>
          <Tag color="blue">已加载 {items.length} 台</Tag>
          {summary.map(([status, count]) => (
            <Tag key={status}>
              {getDeviceStatus(status).label} {count}
            </Tag>
          ))}
        </Space>
        {data.next_cursor ? (
          <Alert
            type="info"
            showIcon
            message="已加载部分设备"
            description="服务端返回了 next_cursor；分页加载将在后续任务接入。"
          />
        ) : null}
        {content}
      </Space>
    );
  } else {
    body = <PageState variant="empty" title="暂无设备数据" />;
  }

  const filters = phase === "forbidden" || phase === "not-found" ? undefined : (
    <DeviceFilters
      keyword={keyword}
      statusFilter={statusFilter}
      typeFilter={typeFilter}
      statusOptions={statusOptions}
      typeOptions={typeOptions}
      disabled={items.length === 0}
      onChange={updateFilter}
      onReset={resetFilters}
    />
  );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Tooltip title="设备创建流程待契约冻结与后端接口接入后开放">
        <span>
          <Button type="primary" icon={<PlusOutlined />} disabled>
            新建设备
          </Button>
        </span>
      </Tooltip>
      <Button
        icon={<ReloadOutlined />}
        loading={refreshing}
        onClick={reload}
      >
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer
      title="设备纳管"
      description="按 DDP-101 设备域契约展示设备档案、状态与标定信息。状态与可用动作以服务端返回为准，前端不做权威权限校验。"
      extra={extra}
      filters={filters}
    >
      {body}
    </PageContainer>
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
