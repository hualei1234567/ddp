import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Grid,
  Input,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import LoadMoreBar from "../../components/devices/LoadMoreBar";
import RobotFormModal from "../../components/devices/RobotFormModal";
import StatusTag from "../../components/devices/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import type { ApiError } from "../../services/api";
import { getDeviceStatus } from "../../services/devicePresentation";
import {
  devicesApi,
  type RobotRecord,
} from "../../services/devices";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  formatCapabilityProfile,
  formatText,
  formatTimestamp,
} from "../../services/format";
import type { PagePhase } from "../../services/pagePhase";

export interface RobotListBodyProps {
  projectId: string;
  phase: PagePhase;
  error: ApiError | null;
  items: RobotRecord[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  stale: boolean;
  updatedAt: number | null;
  isMobile: boolean;
  /** 是否启用了关键词/状态筛选；仅影响空态文案。 */
  hasActiveFilters?: boolean;
  onRetry: () => void;
  onLoadMore: () => void;
  onCreate: () => void;
  createModal?: ReactNode;
  onOpenRobot: (robotId: string) => void;
}

function buildColumns(
  projectId: string,
  onOpenRobot: (robotId: string) => void,
): TableColumnsType<RobotRecord> {
  return [
    {
      title: "机器人",
      key: "name",
      width: 220,
      fixed: "left",
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.name}</Typography.Text>
          <Typography.Text type="secondary">{record.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "机器人 ID",
      dataIndex: "robot_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="机器人 ID"
          to={{
            pathname: `/robots/${encodeURIComponent(value)}`,
            search: `?project_id=${encodeURIComponent(projectId)}`,
          }}
        />
      ),
    },
    {
      title: "类型",
      dataIndex: "robot_type",
      width: 140,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => <StatusTag kind="device" value={value} />,
    },
    {
      title: "厂商 / 型号",
      key: "vendor_model",
      width: 180,
      render: (_value, record) =>
        formatText(
          [record.vendor, record.model]
            .filter((item): item is string => Boolean(item))
            .join(" / "),
        ),
    },
    {
      title: "能力描述",
      dataIndex: "capability_profile",
      width: 200,
      render: (value: Record<string, unknown>) => (
        <Typography.Text ellipsis={{ tooltip: formatCapabilityProfile(value) }}>
          {formatCapabilityProfile(value)}
        </Typography.Text>
      ),
    },
    {
      title: "位置",
      dataIndex: "location_id",
      width: 130,
      render: (value: string | null) => (
        <IdentifierValue value={value} label="位置 ID" />
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      fixed: "right",
      render: (_value, record) => (
        <Button size="small" onClick={() => onOpenRobot(record.robot_id)}>
          查看详情
        </Button>
      ),
    },
  ];
}

/** 机器人列表主体；与请求解耦，便于覆盖 Loading/Empty/Error/Stale。 */
export function RobotListBody({
  projectId,
  phase,
  error,
  items,
  hasMore,
  loading,
  refreshing,
  loadingMore,
  loadMoreError,
  stale,
  updatedAt,
  isMobile,
  hasActiveFilters = false,
  onRetry,
  onLoadMore,
  onCreate,
  createModal,
  onOpenRobot,
}: RobotListBodyProps) {
  const columns = useMemo(
    () => buildColumns(projectId, onOpenRobot),
    [projectId, onOpenRobot],
  );
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;

  if (phase === "forbidden") {
    return <>{errorBody}</>;
  }
  if (phase === "not-found") {
    return (
      <PageState
        variant="empty"
        title="项目不存在或无权查看"
        description="服务端未找到该机器人范围；跨项目资源统一不可见。"
        action={<Button onClick={onRetry}>重试</Button>}
      />
    );
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载机器人"
        description="正在按当前项目加载机器人档案与状态。"
      />
    );
  }
  if (phase === "error") {
    return <>{errorBody}</>;
  }

  const detailPath = (robotId: string) => ({
    pathname: `/robots/${encodeURIComponent(robotId)}`,
    search: `?project_id=${encodeURIComponent(projectId)}`,
  });

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的机器人数据。"
          onRetry={onRetry}
        />
      ) : null}
      {error && !stale ? (
        <ApiErrorState error={error} onRetry={onRetry} compact />
      ) : null}
      <Space wrap>
        <Typography.Text type="secondary">
          更新于 {formatTimestamp(updatedAt)} · 已加载 {items.length} 台机器人
        </Typography.Text>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={onCreate}>
          新建机器人
        </Button>
      </Space>
      {items.length === 0 ? (
        <PageState
          variant="empty"
          title={hasActiveFilters ? "无匹配机器人" : "当前项目还没有机器人"}
          description={
            hasActiveFilters
              ? "已加载数据中没有匹配项；可清空筛选，或继续加载更多后再筛选。"
              : "机器人是设备与传感器的挂载主体；先登记机器人，再关联设备与传感器。"
          }
          action={
            hasActiveFilters ? (
              <Button
                onClick={() => {
                  onRetry();
                }}
              >
                刷新已加载数据
              </Button>
            ) : (
              <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
                新建机器人
              </Button>
            )
          }
        />
      ) : isMobile ? (
        <Space direction="vertical" size={12} className="ddp-stack">
          {items.map((robot) => (
            <Card
              key={robot.robot_id}
              size="small"
              title={
                <Space wrap>
                  <span>{robot.name}</span>
                  <StatusTag kind="device" value={robot.status} />
                </Space>
              }
              extra={<Link to={detailPath(robot.robot_id)}>详情</Link>}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="编码">{robot.code}</Descriptions.Item>
                <Descriptions.Item label="机器人 ID">
                  <IdentifierValue value={robot.robot_id} label="机器人 ID" />
                </Descriptions.Item>
                <Descriptions.Item label="类型">
                  {robot.robot_type}
                </Descriptions.Item>
                <Descriptions.Item label="厂商 / 型号">
                  {formatText(
                    [robot.vendor, robot.model]
                      .filter((item): item is string => Boolean(item))
                      .join(" / "),
                  )}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </Space>
      ) : (
        <Table<RobotRecord>
          rowKey="robot_id"
          columns={columns}
          dataSource={items}
          pagination={false}
          scroll={{ x: 1400 }}
          onRow={(record) => ({
            onClick: () => onOpenRobot(record.robot_id),
          })}
        />
      )}
      <LoadMoreBar
        loadedCount={items.length}
        unit="台机器人"
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

function RobotListContent({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const keyword = searchParams.get("q") ?? "";
  const statusFilter = searchParams.get("status") ?? "";

  const loadRobots = useCallback(
    (options: CursorPageRequestOptions) =>
      devicesApi.listRobots(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status: statusFilter || undefined,
      }),
    [projectId, statusFilter],
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
  } = useCursorPage(loadRobots, (robot) => robot.robot_id);

  const filteredItems = useMemo(() => {
    const text = keyword.trim().toLowerCase();
    if (text === "") {
      return items;
    }
    return items.filter((robot) =>
      [robot.name, robot.code, robot.robot_id, robot.robot_type].some((value) =>
        value.toLowerCase().includes(text),
      ),
    );
  }, [items, keyword]);

  const statusOptions = useMemo(() => {
    const values = new Set(items.map((item) => item.status));
    return [...values]
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: getDeviceStatus(value).label }));
  }, [items]);

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

  const phase = resolvePagePhase({
    hasData: updatedAt !== null,
    loading,
    error,
  });
  const isMobile = screens.md === false;
  const detailPath = (robotId: string) => ({
    pathname: `/robots/${encodeURIComponent(robotId)}`,
    search: `?project_id=${encodeURIComponent(projectId)}`,
  });

  return (
    <>
      <PageContainer
        title="机器人"
        description="机器人是设备与传感器的挂载主体。列表按项目隔离；状态与生命周期动作由服务端校验，前端不做权威权限判断。"
        extra={
          <Space wrap>
            <Typography.Text type="secondary">
              更新于 {formatTimestamp(updatedAt)}
            </Typography.Text>
            <Button
              icon={<ReloadOutlined />}
              loading={refreshing}
              onClick={reload}
            >
              刷新
            </Button>
          </Space>
        }
        filters={
          phase === "forbidden" || phase === "not-found" ? undefined : (
            <Space wrap>
              <Input.Search
                allowClear
                placeholder="按名称 / 编码 / ID 筛选已加载数据"
                defaultValue={keyword}
                onSearch={(value) => updateFilter("q", value.trim())}
                style={{ width: isMobile ? "100%" : 320 }}
              />
              <Select
                allowClear
                placeholder="状态"
                value={statusFilter || undefined}
                options={statusOptions}
                style={{ width: 160 }}
                onChange={(value) => updateFilter("status", value ?? "")}
              />
            </Space>
          )
        }
      >
        <RobotListBody
          projectId={projectId}
          phase={phase}
          error={error}
          items={filteredItems}
          hasMore={hasMore}
          loading={loading}
          refreshing={refreshing}
          loadingMore={loadingMore}
          loadMoreError={loadMoreError}
          stale={stale}
          updatedAt={updatedAt}
          isMobile={isMobile}
          hasActiveFilters={keyword.trim() !== "" || statusFilter !== ""}
          onRetry={reload}
          onLoadMore={loadMore}
          onCreate={() => setCreateOpen(true)}
          onOpenRobot={(robotId) => navigate(detailPath(robotId))}
        />
      </PageContainer>
      <RobotFormModal
        projectId={projectId}
        mode="create"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={(robot) => {
          reload();
          navigate(detailPath(robot.robot_id));
        }}
      />
    </>
  );
}

export default function RobotListPage() {
  const projectId = useProjectId();
  if (!projectId) {
    return (
      <PageContainer title="机器人" description="机器人必须按项目隔离展示。">
        <ProjectScopeRequired scope="机器人列表" />
      </PageContainer>
    );
  }
  return <RobotListContent key={projectId} projectId={projectId} />;
}
