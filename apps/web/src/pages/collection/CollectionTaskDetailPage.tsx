import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Space, Typography } from "antd";
import { useCallback, useMemo, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import CreateBatchModal from "../../components/collection/CreateBatchModal";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { resolveServerActions } from "../../services/collectionPresentation";
import {
  buildTaskListSearch,
  readTaskListFilters,
  resolveTaskDetailTab,
} from "../../services/collectionUrl";
import { collectionsApi } from "../../services/collections";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  describeTaskActionError,
  taskActionSuccess,
} from "../../services/taskActionFeedback";
import type { CollectionBatch } from "../../types/collection";
import CollectionTaskDetailBody from "./taskDetail/CollectionTaskDetailBody";
import type { Feedback } from "./taskDetail/CollectionTaskDetailBody";
import BatchListBody from "./taskDetail/BatchListBody";

function BatchListContainer({
  projectId,
  taskId,
  isMobile,
}: {
  projectId: string;
  taskId: string;
  isMobile: boolean;
}) {
  const [busyBatchId, setBusyBatchId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const loadBatches = useCallback(
    (options: CursorPageRequestOptions) =>
      collectionsApi.listBatches(projectId, taskId, {
        cursor: options.cursor,
        signal: options.signal,
      }),
    [projectId, taskId],
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
  } = useCursorPage(loadBatches, (batch) => batch.batch_id);

  const handleBatchAction = useCallback(
    async (batch: CollectionBatch, action: string, reason: string | null) => {
      setBusyBatchId(batch.batch_id);
      try {
        await collectionsApi.actOnBatch(projectId, batch.batch_id, action, {
          version: batch.version,
          reason,
        });
        reload();
      } finally {
        setBusyBatchId(null);
      }
    },
    [projectId, reload],
  );

  return (
    <BatchListBody
      items={items}
      loading={loading}
      refreshing={refreshing}
      loadingMore={loadingMore}
      hasMore={hasMore}
      error={error}
      loadMoreError={loadMoreError}
      stale={stale}
      isMobile={isMobile}
      busyBatchId={busyBatchId}
      onAction={handleBatchAction}
      onCreate={() => setCreateOpen(true)}
      createModal={
        <CreateBatchModal
          projectId={projectId}
          taskId={taskId}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreated={reload}
        />
      }
      onRetry={reload}
      onLoadMore={loadMore}
    />
  );
}

function CollectionTaskDetailContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = readTaskListFilters(searchParams);
  const activeTab = resolveTaskDetailTab(searchParams.get("tab"));
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const loadTask = useCallback(
    (signal: AbortSignal) =>
      collectionsApi.getTask(projectId, taskId, { signal }),
    [projectId, taskId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(loadTask);

  const actions = useMemo(
    () => (data ? resolveServerActions(data.allowed_actions) : []),
    [data],
  );

  const handleAction = useCallback(
    async (action: string, reason: string | null) => {
      if (!data) {
        return;
      }
      setBusyAction(action);
      setFeedback(null);
      try {
        await collectionsApi.actOnTask(projectId, taskId, action, {
          version: data.version,
          reason,
        });
        setFeedback(taskActionSuccess("服务端已接受该动作。"));
        reload();
      } catch (caught) {
        setFeedback(describeTaskActionError(caught));
      } finally {
        setBusyAction(null);
      }
    },
    [data, projectId, reload, taskId],
  );

  const handleTabChange = useCallback(
    (key: string) => {
      const tab = resolveTaskDetailTab(key);
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (tab === "overview") {
            next.delete("tab");
          } else {
            next.set("tab", tab);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });
  const isMobile = screens.md === false;
  const batchesContent = (
    <BatchListContainer
      projectId={projectId}
      taskId={taskId}
      isMobile={isMobile}
    />
  );

  const title = data ? (
    <Space wrap>
      <span>{data.name}</span>
      <CollectionStatusTag kind="task" value={data.status} />
      <CollectionStatusTag kind="priority" value={data.priority} />
    </Space>
  ) : (
    "采集任务详情"
  );

  const description = data ? (
    <Space wrap size={4}>
      <span>{data.code}</span>
      <span>· 任务 ID：</span>
      <IdentifierValue value={data.task_id} label="任务 ID" />
      <span>· SOP 版本：{data.sop_version ?? "—"}</span>
    </Space>
  ) : (
    "查看任务状态、SOP 快照、批次与数据上传入口。权限与动作以服务端返回为准。"
  );

  const extra = (
    <Space wrap>
      <Typography.Text type="secondary">
        更新于 {formatTimestamp(updatedAt)}
      </Typography.Text>
      <Link to={`/collection/tasks${buildTaskListSearch(projectId, filters)}`}>
        <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
      </Link>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer title={title} description={description} extra={extra}>
      <CollectionTaskDetailBody
        projectId={projectId}
        taskId={taskId}
        filters={filters}
        data={data}
        phase={phase}
        error={error}
        stale={stale}
        activeTab={activeTab}
        actions={actions}
        busyAction={busyAction}
        feedback={feedback}
        batchesContent={batchesContent}
        onTabChange={handleTabChange}
        onAction={handleAction}
        onRetry={reload}
        onBackToList={() =>
          navigate(`/collection/tasks${buildTaskListSearch(projectId, filters)}`)
        }
      />
    </PageContainer>
  );
}

export default function CollectionTaskDetailPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const { taskId } = useParams<{ taskId: string }>();
  const [searchParams] = useSearchParams();
  const filters = readTaskListFilters(searchParams);
  const listPath = `/collection/tasks${buildTaskListSearch(projectId, filters)}`;

  if (!projectId) {
    return (
      <PageContainer title="采集任务详情" description="采集任务必须按项目隔离展示。">
        <PageState
          variant="empty"
          title="未选择项目"
          description="请通过 URL 参数 project_id 指定项目，或在部署环境配置 VITE_PROJECT_ID。"
          action={
            <Button type="primary" onClick={() => navigate("/")}>
              返回工作台
            </Button>
          }
        />
      </PageContainer>
    );
  }

  if (!taskId) {
    return (
      <PageContainer title="采集任务详情">
        <PageState
          variant="empty"
          title="任务 ID 缺失"
          description="当前地址缺少任务 ID，请从任务列表重新进入。"
          action={
            <Button type="primary" onClick={() => navigate(listPath)}>
              返回任务列表
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <CollectionTaskDetailContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
