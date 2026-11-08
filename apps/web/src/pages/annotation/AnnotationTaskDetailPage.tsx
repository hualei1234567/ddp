import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Divider, Space, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import AssignmentTable from "../../components/annotation/AssignmentTable";
import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import BatchTable from "../../components/annotation/BatchTable";
import TaskOverview from "../../components/annotation/TaskOverview";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { buildAnnotationTaskListPath } from "../../services/annotationUrl";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { AnnotationTask } from "../../types/annotation";

function BatchSection({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const loadBatches = useCallback(
    (options: CursorPageRequestOptions) =>
      annotationApi.listBatches(projectId, taskId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, taskId],
  );
  const { items, loading, error, stale, reload } = useCursorPage(
    loadBatches,
    (batch) => batch.batch_id,
  );
  return (
    <BatchTable
      items={items}
      loading={loading}
      error={error}
      stale={stale}
      onRetry={reload}
    />
  );
}

function AssignmentSection({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const loadAssignments = useCallback(
    (options: CursorPageRequestOptions) =>
      annotationApi.listAssignments(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        taskId,
      }),
    [projectId, taskId],
  );
  const { items, loading, error, stale, reload } = useCursorPage(
    loadAssignments,
    (assignment) => assignment.assignment_id,
  );
  return (
    <AssignmentTable
      projectId={projectId}
      items={items}
      loading={loading}
      error={error}
      stale={stale}
      onRetry={reload}
    />
  );
}

function TaskDetailContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const navigate = useNavigate();
  const [refreshToken, setRefreshToken] = useState(0);
  const loadTask = useCallback(
    (signal: AbortSignal) => annotationApi.getTask(projectId, taskId, { signal }),
    [projectId, taskId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadTask);

  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  const refreshAll = () => {
    reload();
    setRefreshToken((value) => value + 1);
  };

  const title = data ? (
    <Space wrap>
      <span>{data.name}</span>
      <AnnotationStatusTag value={data.status} />
      <AnnotationStatusTag value={data.priority} prefix="优先级" />
    </Space>
  ) : (
    "标注任务详情"
  );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Button
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate(buildAnnotationTaskListPath(projectId))}
      >
        返回列表
      </Button>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refreshAll}>
        刷新
      </Button>
    </Space>
  );

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = (
      <PageState
        variant="loading"
        title="正在加载标注任务详情"
        description="正在读取任务、Schema 快照与进度。"
      />
    );
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const task: AnnotationTask = data;
    body = (
      <Space direction="vertical" size={16} className="ddp-stack">
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，当前展示的是上次成功获取的任务数据。"
            onRetry={reload}
          />
        ) : null}
        <TaskOverview projectId={projectId} task={task} />
        <Divider orientation="left">批次</Divider>
        <BatchSection
          key={`${taskId}:batches:${refreshToken}`}
          projectId={projectId}
          taskId={taskId}
        />
        <Divider orientation="left">分配与工作量</Divider>
        <AssignmentSection
          key={`${taskId}:assignments:${refreshToken}`}
          projectId={projectId}
          taskId={taskId}
        />
        <Typography.Text type="secondary">
          任务动作（审批、就绪、开始、暂停等）不在本页前端范围；服务端未下发
          allowed_actions 时不根据状态推断按钮。
        </Typography.Text>
      </Space>
    );
  }

  return (
    <PageContainer
      title={title}
      description={data ? `${data.code} · 任务 ID：${data.task_id}` : "查看任务、批次与分配。"}
      extra={extra}
    >
      {body}
    </PageContainer>
  );
}

export default function AnnotationTaskDetailPage() {
  const projectId = useProjectId();
  const { taskId } = useParams<{ taskId: string }>();

  if (!projectId) {
    return (
      <PageContainer title="标注任务详情" description="标注任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注任务详情" />
      </PageContainer>
    );
  }
  if (!taskId) {
    return (
      <PageContainer title="标注任务详情">
        <ProjectScopeRequired scope="标注任务详情" />
      </PageContainer>
    );
  }
  return (
    <TaskDetailContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
