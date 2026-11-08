import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Space } from "antd";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import CollectionStatusTag from "../../components/collection/CollectionStatusTag";
import IdentifierValue from "../../components/ui/IdentifierValue";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { useUploadQueue } from "../../hooks/useUploadQueue";
import { resolveServerActions } from "../../services/collectionPresentation";
import {
  buildTaskDetailPath,
  buildTaskListSearch,
  readTaskListFilters,
  resolveWorkbenchStep,
  type WorkbenchStep,
} from "../../services/collectionUrl";
import { collectionsApi } from "../../services/collections";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import {
  describeTaskActionError,
  taskActionSuccess,
  type TaskActionFeedback,
} from "../../services/taskActionFeedback";
import {
  createEmptyCheckResults,
  SELF_CHECK_ITEMS,
  WORKBENCH_CHECK_ITEMS,
  type CheckResults,
} from "../../services/workbenchChecklist";
import WorkbenchBody from "./workbench/WorkbenchBody";

function WorkbenchContent({
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
  const step = resolveWorkbenchStep(searchParams.get("step"));
  const online = useOnlineStatus();
  const [checklistResults, setChecklistResults] = useState<CheckResults>(() =>
    createEmptyCheckResults(WORKBENCH_CHECK_ITEMS),
  );
  const [selfCheckResults, setSelfCheckResults] = useState<CheckResults>(() =>
    createEmptyCheckResults(SELF_CHECK_ITEMS),
  );
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<TaskActionFeedback | null>(null);
  const uploadQueue = useUploadQueue({ projectId, taskId });

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

  const handleStepChange = useCallback(
    (nextStep: WorkbenchStep) => {
      setSearchParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (nextStep === "confirm") {
            next.delete("step");
          } else {
            next.set("step", nextStep);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
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

  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });
  const isMobile = screens.md === false;

  const title = data ? (
    <Space wrap>
      <span>现场工作台 · {data.name}</span>
      <CollectionStatusTag kind="task" value={data.status} />
    </Space>
  ) : (
    "现场工作台"
  );

  const description = data ? (
    <Space wrap size={4}>
      <span>{data.code}</span>
      <span>· 任务 ID：</span>
      <IdentifierValue value={data.task_id} label="任务 ID" />
      <span>· 更新于 {formatTimestamp(updatedAt)}</span>
    </Space>
  ) : (
    "完成任务确认、开工检查、采集、上传与自检提交。状态与动作以服务端为准。"
  );

  const extra = (
    <Space wrap>
      <Link to={buildTaskDetailPath(projectId, taskId, filters)}>
        <Button icon={<ArrowLeftOutlined />}>退出工作台</Button>
      </Link>
      <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
        刷新
      </Button>
    </Space>
  );

  return (
    <PageContainer title={title} description={description} extra={extra}>
      <WorkbenchBody
        projectId={projectId}
        taskId={taskId}
        filters={filters}
        data={data}
        phase={phase}
        error={error}
        stale={stale}
        step={step}
        online={online}
        actions={actions}
        checklistResults={checklistResults}
        selfCheckResults={selfCheckResults}
        uploadQueue={uploadQueue}
        busyAction={busyAction}
        feedback={feedback}
        isMobile={isMobile}
        onStepChange={handleStepChange}
        onCheckChange={(key, result) =>
          setChecklistResults((previous) => ({ ...previous, [key]: result }))
        }
        onRecheck={() =>
          setChecklistResults(createEmptyCheckResults(WORKBENCH_CHECK_ITEMS))
        }
        onSelfCheckChange={(key, passed) =>
          setSelfCheckResults((previous) => ({
            ...previous,
            [key]: passed ? "pass" : "pending",
          }))
        }
        onAction={handleAction}
        onRetry={reload}
        onBackToList={() =>
          navigate(`/collection/tasks${buildTaskListSearch(projectId, filters)}`)
        }
      />
    </PageContainer>
  );
}

export default function WorkbenchPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const { taskId } = useParams<{ taskId: string }>();
  const [searchParams] = useSearchParams();
  const filters = readTaskListFilters(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="现场工作台" description="工作台必须按项目隔离展示。">
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
      <PageContainer title="现场工作台">
        <PageState
          variant="empty"
          title="任务 ID 缺失"
          description="当前地址缺少任务 ID，请从任务列表重新进入。"
          action={
            <Button
              type="primary"
              onClick={() =>
                navigate(
                  `/collection/tasks${buildTaskListSearch(projectId, filters)}`,
                )
              }
            >
              返回任务列表
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <WorkbenchContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
