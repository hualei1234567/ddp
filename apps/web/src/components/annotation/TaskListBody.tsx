import { Card, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import type { ApiError } from "../../services/api";
import {
  describeDueDistance,
  presentProgress,
} from "../../services/annotationPresentation";
import { buildAnnotationTaskDetailPath } from "../../services/annotationUrl";
import { formatDateTime } from "../../services/format";
import type { AnnotationTask } from "../../types/annotation";
import LoadMoreBar from "../devices/LoadMoreBar";
import ApiErrorState from "../ui/ApiErrorState";
import IdentifierValue from "../ui/IdentifierValue";
import PageState from "../ui/PageState";
import AnnotationStatusTag from "./AnnotationStatusTag";

function columns(projectId: string): TableColumnsType<AnnotationTask> {
  return [
    {
      title: "任务",
      key: "name",
      width: 220,
      fixed: "left",
      render: (_value, task) => (
        <Space direction="vertical" size={0}>
          <Link to={buildAnnotationTaskDetailPath(projectId, task.task_id)}>
            <Typography.Text strong>{task.name}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">{task.code}</Typography.Text>
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
          to={buildAnnotationTaskDetailPath(projectId, value)}
        />
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 130,
      render: (value: string) => <AnnotationStatusTag value={value} />,
    },
    {
      title: "优先级",
      dataIndex: "priority",
      width: 90,
    },
    {
      title: "Schema / 目标",
      key: "schema",
      width: 190,
      render: (_value, task) => (
        <Space direction="vertical" size={0}>
          <span>Schema v{task.schema_version}</span>
          <Typography.Text type="secondary">
            目标 {task.target_item_count} 项 / {task.target_episode_count} Episode
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "进度",
      key: "progress",
      width: 200,
      render: (_value, task) => (
        <Space direction="vertical" size={0}>
          <span>已提交 {presentProgress(task.progress, "submitted_items")}</span>
          <Typography.Text type="secondary">
            已通过 {presentProgress(task.progress, "approved_items")} · 返工{" "}
            {presentProgress(task.progress, "rework_items")}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "截止时间",
      dataIndex: "due_at",
      width: 210,
      render: (value: string | null, task) => {
        const distance = describeDueDistance(value, task.is_overdue);
        return (
          <Space direction="vertical" size={0}>
            <Space size={4} wrap>
              <span>{formatDateTime(value)}</span>
              {task.is_overdue ? <Tag color="red">已逾期</Tag> : null}
            </Space>
            {distance ? (
              <Typography.Text type={task.is_overdue ? "danger" : "secondary"}>
                {distance}
              </Typography.Text>
            ) : null}
          </Space>
        );
      },
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      width: 170,
      render: (value: string | null) => formatDateTime(value),
    },
  ];
}

export interface TaskListBodyProps {
  projectId: string;
  items: AnnotationTask[];
  loading: boolean;
  refreshing: boolean;
  error: ApiError | null;
  stale: boolean;
  isMobile: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  loadMoreError: ApiError | null;
  onRetry: () => void;
  onLoadMore: () => void;
}

export default function TaskListBody({
  projectId,
  items,
  loading,
  refreshing,
  error,
  stale,
  isMobile,
  hasMore,
  loadingMore,
  loadMoreError,
  onRetry,
  onLoadMore,
}: TaskListBodyProps) {
  if (loading) {
    return (
      <PageState
        variant="loading"
        title="正在加载标注任务"
        description="正在按当前项目读取标注任务。"
      />
    );
  }
  if (error && items.length === 0) {
    return <ApiErrorState error={error} onRetry={onRetry} />;
  }
  if (items.length === 0) {
    return (
      <PageState
        variant="empty"
        title="当前项目还没有标注任务"
        description="可从“新建任务”创建，或在筛选条件中调整状态。"
      />
    );
  }

  const overdueCount = items.filter((task) => task.is_overdue).length;

  return (
    <Space direction="vertical" size={12} className="ddp-stack">
      {stale ? (
        <PageState
          compact
          variant="stale"
          description="最近一次刷新失败，当前展示的是上次成功获取的标注任务。"
          onRetry={onRetry}
        />
      ) : null}
      {isMobile ? (
        <Space direction="vertical" size={12} className="ddp-stack">
          {items.map((task) => (
            <Card
              key={task.task_id}
              title={
                <Space wrap>
                  <Link to={buildAnnotationTaskDetailPath(projectId, task.task_id)}>
                    {task.name}
                  </Link>
                  <AnnotationStatusTag value={task.status} />
                </Space>
              }
              extra={<span>{task.priority}</span>}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="编码">{task.code}</Descriptions.Item>
                <Descriptions.Item label="任务 ID">
                  <IdentifierValue
                    value={task.task_id}
                    label="任务 ID"
                    to={buildAnnotationTaskDetailPath(projectId, task.task_id)}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="Schema">
                  v{task.schema_version}
                </Descriptions.Item>
                <Descriptions.Item label="进度">
                  已提交 {presentProgress(task.progress, "submitted_items")}；已通过{" "}
                  {presentProgress(task.progress, "approved_items")}
                </Descriptions.Item>
                <Descriptions.Item label="截止时间">
                  <Space direction="vertical" size={0}>
                    <Space size={4} wrap>
                      <span>{formatDateTime(task.due_at)}</span>
                      {task.is_overdue ? <Tag color="red">已逾期</Tag> : null}
                    </Space>
                    {describeDueDistance(task.due_at, task.is_overdue) ? (
                      <Typography.Text
                        type={task.is_overdue ? "danger" : "secondary"}
                      >
                        {describeDueDistance(task.due_at, task.is_overdue)}
                      </Typography.Text>
                    ) : null}
                  </Space>
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </Space>
      ) : (
        <Table<AnnotationTask>
          rowKey="task_id"
          columns={columns(projectId)}
          dataSource={items}
          pagination={false}
          scroll={{ x: 1400 }}
        />
      )}
      <Space wrap size={12}>
        <Space size={4}>
          <Typography.Text strong>逾期任务数</Typography.Text>
          <Tag color={overdueCount > 0 ? "red" : "default"}>{overdueCount}</Tag>
          <Typography.Text type="secondary">
            / 已加载 {items.length} 条（当前已加载范围的汇总）
          </Typography.Text>
        </Space>
      </Space>
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
