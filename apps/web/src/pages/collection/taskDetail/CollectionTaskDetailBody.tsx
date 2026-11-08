import {
  Alert,
  Button,
  Card,
  Space,
  Tabs,
} from "antd";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import SopPanel from "../../../components/collection/SopPanel";
import TaskActionBar from "../../../components/collection/TaskActionBar";
import ApiErrorState from "../../../components/ui/ApiErrorState";
import PageState from "../../../components/ui/PageState";
import { resolveServerActions } from "../../../services/collectionPresentation";
import {
  buildTaskListSearch,
  buildWorkbenchPath,
  type TaskDetailTab,
  type TaskListFilters,
} from "../../../services/collectionUrl";
import type { PagePhase } from "../../../services/pagePhase";
import type { ApiError } from "../../../services/api";
import type { CollectionTask } from "../../../types/collection";
import TaskOverview from "./TaskOverview";

export interface Feedback {
  kind: "success" | "warning" | "error";
  title: string;
  description: string;
}

export interface CollectionTaskDetailBodyProps {
  projectId: string;
  taskId: string;
  filters: TaskListFilters;
  data: CollectionTask | null;
  phase: PagePhase;
  error: ApiError | null;
  stale: boolean;
  activeTab: TaskDetailTab;
  actions: ReturnType<typeof resolveServerActions>;
  busyAction: string | null;
  feedback: Feedback | null;
  batchesContent: ReactNode;
  onTabChange: (key: string) => void;
  onAction: (action: string, reason: string | null) => void;
  onRetry: () => void;
  onBackToList: () => void;
}

export default function CollectionTaskDetailBody({
  projectId,
  taskId,
  filters,
  data,
  phase,
  error,
  stale,
  activeTab,
  actions,
  busyAction,
  feedback,
  batchesContent,
  onTabChange,
  onAction,
  onRetry,
  onBackToList,
}: CollectionTaskDetailBodyProps) {
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
        title="任务不存在或无权查看"
        description="服务端未找到该任务；该任务可能属于其他项目，或已被移除。跨项目资源统一不可见。"
        action={<Button onClick={onBackToList}>返回任务列表</Button>}
      />
    );
  }
  if (phase === "loading") {
    return (
      <PageState
        variant="loading"
        title="正在加载采集任务详情"
        description="正在读取任务、SOP 快照、批次与动作权限。"
      />
    );
  }
  if (phase === "error") {
    return <>{errorBody}</>;
  }
  if (!data) {
    return <PageState variant="empty" title="暂无任务详情" />;
  }

  const feedbackNode = feedback ? (
    <Alert
      type={feedback.kind}
      showIcon
      message={feedback.title}
      description={feedback.description}
      action={
        feedback.kind === "warning" ? (
          <Button size="small" onClick={onRetry}>
            刷新任务
          </Button>
        ) : undefined
      }
    />
  ) : null;

  const tabs = [
    {
      key: "overview",
      label: "概览",
      children: <TaskOverview task={data} />,
    },
    {
      key: "sop",
      label: "SOP",
      children: (
        <SopPanel sopId={data.sop_id} sopVersion={data.sop_version} />
      ),
    },
    {
      key: "batches",
      label: "批次",
      children: batchesContent,
    },
    {
      key: "uploads",
      label: "数据与上传",
      children: (
        <Space direction="vertical" size={12} className="ddp-stack">
          <Alert
            type="info"
            showIcon
            message="上传在采集员工作台完成"
            description="上传进度、断点续传、失败重试与服务端校验状态集中展示在工作台；这里不重复伪造上传成功。"
          />
          <Link to={buildWorkbenchPath(projectId, taskId, "upload", filters)}>
            <Button type="primary">进入工作台上传步骤</Button>
          </Link>
        </Space>
      ),
    },
    {
      key: "operations",
      label: "操作记录",
      children: (
        <PageState
          variant="disabled"
          compact
          title="操作记录接口尚未定义"
          description="DDP-301 草案未提供任务操作记录端点；审计时间线需在契约冻结后接入，当前不展示推测数据。"
        />
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} className="ddp-stack">
      {stale ? (
        <PageState
          variant="stale"
          compact
          description="最近一次刷新失败，当前展示的是上次成功获取的任务详情；状态可能已变化。"
          onRetry={onRetry}
        />
      ) : null}
      {feedbackNode}
      <Card title="可执行动作">
        <Space direction="vertical" size={12} className="ddp-stack">
          <TaskActionBar
            actions={actions}
            busyAction={busyAction}
            getActionDisabledReason={(action) =>
              action === "start"
                ? "请在现场工作台完成开工检查后开始采集"
                : null
            }
            onAction={onAction}
          />
          <Space wrap>
            <Link to={buildWorkbenchPath(projectId, taskId, undefined, filters)}>
              <Button>进入现场工作台</Button>
            </Link>
            <Link
              to={`/collection/tasks${buildTaskListSearch(projectId, filters)}`}
            >
              <Button>返回列表</Button>
            </Link>
          </Space>
        </Space>
      </Card>
      <Card>
        <Tabs activeKey={activeTab} onChange={onTabChange} items={tabs} />
      </Card>
    </Space>
  );
}
