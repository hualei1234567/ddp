import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Divider, Space, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionButtons from "../../components/annotation/ActionButtons";
import type { AnnotationActionPayload } from "../../components/annotation/ActionButtons";
import AnnotationFeedbackAlert from "../../components/annotation/AnnotationFeedbackAlert";
import AnnotationStatusTag from "../../components/annotation/AnnotationStatusTag";
import AssignmentCreateDrawer from "../../components/annotation/AssignmentCreateDrawer";
import AssignmentTable from "../../components/annotation/AssignmentTable";
import BatchCreateDrawer from "../../components/annotation/BatchCreateDrawer";
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
import type { WithAllowedActions } from "../../services/annotationAllowedActions";
import { resolveAnnotationActions } from "../../services/annotationActions";
import {
  annotationSuccess,
  describeAnnotationActionError,
  type AnnotationFeedback,
} from "../../services/annotationPresentation";
import type {
  AnnotationAssignmentCreateInput,
  AnnotationBatchCreateInput,
} from "../../services/annotationTaskApi";
import { buildAnnotationTaskListPath } from "../../services/annotationUrl";
import { fetchAllCursorPages } from "../../services/cursorPage";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type {
  AnnotationAssignment,
  AnnotationBatch,
} from "../../types/annotation";

function BatchSection({
  projectId,
  taskId,
  busyAction,
  onAction,
}: {
  projectId: string;
  taskId: string;
  busyAction: string | null;
  onAction: (
    batch: WithAllowedActions<AnnotationBatch>,
    action: string,
    payload: AnnotationActionPayload,
  ) => Promise<void>;
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
      busyAction={busyAction}
      onAction={onAction}
    />
  );
}

function AssignmentSection({
  projectId,
  taskId,
  busyAction,
  onAction,
}: {
  projectId: string;
  taskId: string;
  busyAction: string | null;
  onAction: (
    item: WithAllowedActions<AnnotationAssignment>,
    action: string,
    payload: AnnotationActionPayload,
  ) => Promise<void>;
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
      busyAction={busyAction}
      onAction={onAction}
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
  const [feedback, setFeedback] = useState<AnnotationFeedback | null>(null);
  const [busyTaskAction, setBusyTaskAction] = useState<string | null>(null);
  const [busyRowAction, setBusyRowAction] = useState<string | null>(null);
  const [createBatchOpen, setCreateBatchOpen] = useState(false);
  const [createAssignmentOpen, setCreateAssignmentOpen] = useState(false);
  const [batchesForAssignment, setBatchesForAssignment] = useState<
    AnnotationBatch[]
  >([]);
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

  const handleTaskAction = async (
    action: string,
    payload: AnnotationActionPayload,
  ) => {
    if (data === null) {
      throw new Error("任务尚未加载完成，请刷新后重试。");
    }
    setBusyTaskAction(action);
    setFeedback(null);
    try {
      await annotationApi.actOnTask(projectId, taskId, action, {
        version: data.version,
        reason: payload.reason,
        waiverReason: payload.waiverReason ?? null,
      });
      setFeedback(annotationSuccess(`任务动作 ${action} 已提交。`));
      refreshAll();
    } catch (caught) {
      setFeedback(describeAnnotationActionError(caught));
      throw caught;
    } finally {
      setBusyTaskAction(null);
    }
  };

  const handleBatchAction = async (
    batch: WithAllowedActions<AnnotationBatch>,
    action: string,
    payload: AnnotationActionPayload,
  ) => {
    setBusyRowAction(`${batch.batch_id}:${action}`);
    setFeedback(null);
    try {
      await annotationApi.actOnBatch(projectId, batch.batch_id, action, {
        version: batch.version,
        reason: payload.reason,
      });
      setFeedback(annotationSuccess(`批次动作 ${action} 已提交。`));
      refreshAll();
    } catch (caught) {
      setFeedback(describeAnnotationActionError(caught));
      throw caught;
    } finally {
      setBusyRowAction(null);
    }
  };

  const handleAssignmentAction = async (
    item: WithAllowedActions<AnnotationAssignment>,
    action: string,
    payload: AnnotationActionPayload,
  ) => {
    setBusyRowAction(`${item.assignment_id}:${action}`);
    setFeedback(null);
    try {
      await annotationApi.actOnAssignment(projectId, item.assignment_id, action, {
        version: item.version,
        reason: payload.reason,
      });
      setFeedback(annotationSuccess(`分配动作 ${action} 已提交。`));
      refreshAll();
    } catch (caught) {
      setFeedback(describeAnnotationActionError(caught));
      throw caught;
    } finally {
      setBusyRowAction(null);
    }
  };

  const handleCreateBatch = async (input: AnnotationBatchCreateInput) => {
    await annotationApi.createBatch(projectId, taskId, input);
    setCreateBatchOpen(false);
    setFeedback(annotationSuccess("批次已创建。"));
    refreshAll();
  };

  const openAssignmentDrawer = async () => {
    setCreateAssignmentOpen(true);
    setFeedback(null);
    try {
      const batches = await fetchAllCursorPages(
        (options) =>
          annotationApi.listBatches(projectId, taskId, {
            cursor: options.cursor,
            limit: options.limit,
          }),
        (batch) => batch.batch_id,
      );
      setBatchesForAssignment(batches);
    } catch (caught) {
      setBatchesForAssignment([]);
      setFeedback(describeAnnotationActionError(caught));
    }
  };

  const handleCreateAssignment = async (
    input: AnnotationAssignmentCreateInput,
  ) => {
    await annotationApi.createAssignment(projectId, input);
    setCreateAssignmentOpen(false);
    setFeedback(annotationSuccess("分配已创建。"));
    refreshAll();
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
    const task = data;
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
        <AnnotationFeedbackAlert feedback={feedback} />
        <TaskOverview projectId={projectId} task={task} />
        <Space direction="vertical" size={8} className="ddp-stack">
          <Typography.Text strong>任务动作</Typography.Text>
          <ActionButtons
            kind="task"
            actions={resolveAnnotationActions("task", task.status, task.allowed_actions).actions}
            busyAction={busyTaskAction}
            waiverActions={["complete"]}
            showFallbackNote
            onAction={handleTaskAction}
          />
        </Space>
        <Divider orientation="left">
          <Space>
            批次
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => setCreateBatchOpen(true)}
            >
              新建批次
            </Button>
          </Space>
        </Divider>
        <BatchSection
          key={`${taskId}:batches:${refreshToken}`}
          projectId={projectId}
          taskId={taskId}
          busyAction={busyRowAction}
          onAction={handleBatchAction}
        />
        <Divider orientation="left">
          <Space>
            分配与工作量
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => void openAssignmentDrawer()}
            >
              新建指派
            </Button>
          </Space>
        </Divider>
        <AssignmentSection
          key={`${taskId}:assignments:${refreshToken}`}
          projectId={projectId}
          taskId={taskId}
          busyAction={busyRowAction}
          onAction={handleAssignmentAction}
        />
      </Space>
    );
  }

  return (
    <PageContainer
      title={title}
      description={
        data
          ? `${data.code} · 任务 ID：${data.task_id}`
          : "查看任务、批次与分配。"
      }
      extra={extra}
    >
      {body}
      <BatchCreateDrawer
        open={createBatchOpen}
        onClose={() => setCreateBatchOpen(false)}
        onSubmit={handleCreateBatch}
      />
      <AssignmentCreateDrawer
        open={createAssignmentOpen}
        batches={batchesForAssignment}
        onClose={() => setCreateAssignmentOpen(false)}
        onSubmit={handleCreateAssignment}
      />
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
