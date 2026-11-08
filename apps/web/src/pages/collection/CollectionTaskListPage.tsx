import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Grid,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useCallback, useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import TaskFilters from "../../components/collection/TaskFilters";
import LoadMoreBar from "../../components/devices/LoadMoreBar";
import ApiErrorState from "../../components/ui/ApiErrorState";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import type { ApiError } from "../../services/api";
import {
  buildCountProgress,
  formatDurationSeconds,
  getPriority,
  getTaskStatus,
} from "../../services/collectionPresentation";
import {
  buildTaskDetailPath,
  buildWorkbenchPath,
  hasTaskListFilters,
  readTaskListFilters,
  type TaskListFilters,
} from "../../services/collectionUrl";
import { collectionsApi } from "../../services/collections";
import { formatDateTime, formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { PagePhase } from "../../services/pagePhase";
import {
  COLLECTION_PRIORITY_VALUES,
  COLLECTION_TASK_STATUS_VALUES,
  type CollectionTask,
} from "../../types/collection";

const STATUS_OPTIONS = COLLECTION_TASK_STATUS_VALUES.map((value) => ({
  value,
  label: getTaskStatus(value).label,
}));

const PRIORITY_OPTIONS = COLLECTION_PRIORITY_VALUES.map((value) => ({
  value,
  label: getPriority(value).label,
}));

function filterTasks(
  items: CollectionTask[],
  filters: TaskListFilters,
): CollectionTask[] {
  const keyword = filters.keyword.toLowerCase();
  return items.filter((item) => {
    if (filters.priority && item.priority !== filters.priority) {
      return false;
    }
    if (keyword === "") {
      return true;
    }
    return [item.name, item.code, item.task_id].some((value) =>
      value.toLowerCase().includes(keyword),
    );
  });
}

function progressText(task: CollectionTask): string {
  const submitted = buildCountProgress(
    task.submitted_episode_count,
    task.target_episode_count,
    "条",
  );
  const accepted = buildCountProgress(
    task.accepted_episode_count,
    task.target_episode_count,
    "条",
  );
  return `已提交 ${submitted.text}；已验收 ${accepted.text}`;
}

function buildColumns(
  projectId: string,
  filters: TaskListFilters,
): TableColumnsType<CollectionTask> {
  return [
    {
      title: "任务",
      key: "name",
      width: 220,
      fixed: "left",
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <Link
            to={buildTaskDetailPath(projectId, record.task_id, filters)}
            onClick={(event) => event.stopPropagation()}
          >
            <Typography.Text strong>{record.name}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{record.code}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "任务 ID",
      dataIndex: "task_id",
      width: 210,
      render: (value: string) => (
        <IdentifierValue
          value={value}
          label="任务 ID"
          to={buildTaskDetailPath(projectId, value, filters)}
        />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => (
        <CollectionStatusTag kind="task" value={value} />
      ),
    },
    {
      title: "优先级",
      dataIndex: "priority",
      width: 90,
      render: (value: string) => (
        <CollectionStatusTag kind="priority" value={value} />
      ),
    },
    {
      title: "负责人 / 机器人",
      key: "assignment",
      width: 190,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <IdentifierValue value={record.assigned_to} label="负责人 ID" />
          <IdentifierValue
            value={record.assigned_robot_id}
            label="机器人 ID"
          />
        </Space>
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
      title: "目标与进度",
      key: "progress",
      width: 230,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>{progressText(record)}</span>
          <Typography.Text type="secondary">
            目标时长 {formatDurationSeconds(record.target_duration_sec)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      sorter: (left, right) => left.updated_at.localeCompare(right.updated_at),
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 150,
      fixed: "right",
      render: (_value, record) => (
        <Space direction="vertical" size={2}>
          <Link to={buildTaskDetailPath(projectId, record.task_id, filters)}>
            查看详情
          </Link>
          <Link to={buildWorkbenchPath(projectId, record.task_id, undefined, filters)}>
            现场工作台
          </Link>
        </Space>
      ),
    },
  ];
}

export interface CollectionTaskListBodyProps {
  projectId: string;
  filters: TaskListFilters;
  phase: PagePhase;
  error: ApiError | null;
  items: CollectionTask[];
  filteredItems: CollectionTask[];
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  stale: boolean;
  updatedAt: number | null;
  isMobile: boolean;
  onRetry: () => void;
  onResetFilters: () => void;
  onLoadMore: () => void;
}

export function CollectionTaskListBody({
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
}: CollectionTaskListBodyProps) {
  const filtersActive = hasTaskListFilters(filters);
  const columns = useMemo(
    () => buildColumns(projectId, filters),
    [projectId, filters],
  );
  const errorBody = error ? (
    <ApiErrorState error={error} onRetry={onRetry} />
  ) : null;

  const summary = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
    }
    return [...counts.entries()];
  }, [items]);

  let body: ReactNode;
  if (phase === "forbidden") {
    body = errorBody;
  } else if (phase === "not-found") {
    body = (
      <PageState
        variant="empty"
        title="项目不存在或无权查看"
        description="服务端未找到该采集任务范围；跨项目资源统一不可见。"
        action={<Button onClick={onRetry}>重试</Button>}
      />
    );
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载采集任务"
        description="正在按当前项目加载任务与分配信息。"
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
          title={filtersActive ? "无匹配任务" : "当前项目还没有采集任务"}
          description={
            filtersActive
              ? hasMore
                ? "已加载任务中没有匹配项；还有更多任务未加载，可继续加载后再筛选。"
                : "当前筛选条件下没有任务，可清空筛选后重试。"
              : "创建任务接口属于阶段 2 后续实现范围；服务端端点未实现时不应伪造任务数据。"
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
          title="无匹配任务"
          description={
            hasMore
              ? "已加载的任务中没有匹配项；还有更多任务未加载，可继续加载后再筛选。"
              : "当前筛选条件下没有任务，可清空筛选后重试。"
          }
          action={<Button onClick={onResetFilters}>清空筛选</Button>}
        />
      );
    } else if (isMobile) {
      content = (
        <Space direction="vertical" size={12} className="ddp-stack">
          {filteredItems.map((task) => (
            <Card
              key={task.task_id}
              title={
                <Space wrap>
                  <span>{task.name}</span>
                  <CollectionStatusTag kind="task" value={task.status} />
                  <CollectionStatusTag kind="priority" value={task.priority} />
                </Space>
              }
              extra={
                <Link to={buildTaskDetailPath(projectId, task.task_id, filters)}>
                  详情
                </Link>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="编码">
                  {task.code}
                </Descriptions.Item>
                <Descriptions.Item label="任务 ID">
                  <IdentifierValue
                    value={task.task_id}
                    label="任务 ID"
                    to={buildTaskDetailPath(projectId, task.task_id, filters)}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="负责人">
                  <IdentifierValue value={task.assigned_to} label="负责人 ID" />
                </Descriptions.Item>
                <Descriptions.Item label="计划时间">
                  {formatDateTime(task.scheduled_start)} 至{" "}
                  {formatDateTime(task.scheduled_end)}
                </Descriptions.Item>
                <Descriptions.Item label="目标与进度">
                  {progressText(task)}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {formatDateTime(task.updated_at)}
                </Descriptions.Item>
              </Descriptions>
              <Space wrap>
                <Link
                  to={buildWorkbenchPath(
                    projectId,
                    task.task_id,
                    undefined,
                    filters,
                  )}
                >
                  <Button>进入现场工作台</Button>
                </Link>
              </Space>
            </Card>
          ))}
        </Space>
      );
    } else {
      content = (
        <Table<CollectionTask>
          rowKey="task_id"
          columns={columns}
          dataSource={filteredItems}
          pagination={false}
          scroll={{ x: 1600 }}
        />
      );
    }

    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            variant="stale"
            compact
            description="最近一次刷新失败，当前展示的是上次成功获取的任务数据。"
            onRetry={onRetry}
          />
        ) : null}
        {error && !stale ? (
          <ApiErrorState error={error} onRetry={onRetry} compact />
        ) : null}
        <Space wrap>
          <Tag color="blue">已加载 {items.length} 条任务</Tag>
          {summary.map(([status, count]) => (
            <Tag key={status}>
              {getTaskStatus(status).label} {count}
            </Tag>
          ))}
        </Space>
        <Typography.Text type="secondary">
          {hasMore
            ? `统计为已加载 ${items.length} 条任务；关键词与优先级只作用于该范围。`
            : `统计覆盖当前项目全部 ${items.length} 条任务；关键词与优先级作用于该范围。`}
        </Typography.Text>
        {content}
        <LoadMoreBar
          loadedCount={items.length}
          unit="条任务"
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

function CollectionTaskListContent({
  projectId,
  filters,
}: {
  projectId: string;
  filters: TaskListFilters;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams] = useSearchParams();

  const loadTasks = useCallback(
    (options: CursorPageRequestOptions) =>
      collectionsApi.listTasks(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status: filters.status || undefined,
        assignedTo: filters.assignedTo || undefined,
      }),
    [projectId, filters.status, filters.assignedTo],
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
  } = useCursorPage(loadTasks, (task) => task.task_id);

  const filteredItems = useMemo(
    () => filterTasks(items, filters),
    [items, filters],
  );
  const phase = resolvePagePhase({
    hasData: updatedAt !== null,
    loading,
    error,
  });
  const isMobile = screens.md === false;

  const updateFilter = useCallback(
    (key: keyof TaskListFilters, value: string) => {
      const params = new URLSearchParams(searchParams);
      const paramKey =
        key === "keyword"
          ? "q"
          : key === "assignedTo"
            ? "assigned_to"
            : key;
      if (value) {
        params.set(paramKey, value);
      } else {
        params.delete(paramKey);
      }
      const query = params.toString();
      navigate(
        {
          pathname: "/collection/tasks",
          search: query ? `?${query}` : "",
        },
        { replace: true },
      );
    },
    [navigate, searchParams],
  );

  const resetFilters = useCallback(() => {
    const params = new URLSearchParams();
    const projectIdParam = searchParams.get("project_id");
    if (projectIdParam) {
      params.set("project_id", projectIdParam);
    }
    const query = params.toString();
    navigate(
      {
        pathname: "/collection/tasks",
        search: query ? `?${query}` : "",
      },
      { replace: true },
    );
  }, [navigate, searchParams]);

  const filtersElement =
    phase === "forbidden" ? undefined : (
      <TaskFilters
        filters={filters}
        statusOptions={STATUS_OPTIONS}
        priorityOptions={PRIORITY_OPTIONS}
        loadedCount={items.length}
        hasMore={hasMore}
        disabled={items.length === 0}
        onChange={updateFilter}
        onReset={resetFilters}
        layout={isMobile ? "stacked" : "inline"}
      />
    );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Tooltip title="任务创建表单与接口不在 DDP-305 前端范围内；阶段 2 端点尚未实现">
        <span>
          <Button type="primary" icon={<PlusOutlined />} disabled>
            新建任务
          </Button>
        </span>
      </Tooltip>
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
    <PageContainer
      title="采集任务"
      description="按阶段 2 契约草案（DRAFT）展示采集任务。状态与可执行动作以服务端为准；任务创建接口尚未实现时，页面只呈现真实的加载/错误/禁用状态，不使用 Mock 数据冒充业务结果。"
      extra={extra}
      filters={filtersElement}
    >
      <Alert
        type="info"
        showIcon
        message="阶段 2 契约草案，后端端点尚未实现"
        description="本页按 DDP-301 草案接入；若后端返回 404/501，页面会展示 Error 与错误码，而不是伪造任务数据。"
      />
      <CollectionTaskListBody
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
      />
    </PageContainer>
  );
}

export default function CollectionTaskListPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const filters = readTaskListFilters(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="采集任务" description="采集任务必须按项目隔离展示。">
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

  return (
    <CollectionTaskListContent
      key={`${projectId}:${filters.status}:${filters.assignedTo}`}
      projectId={projectId}
      filters={filters}
    />
  );
}
