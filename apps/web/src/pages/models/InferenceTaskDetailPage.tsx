import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Descriptions, Divider, Space, Typography } from "antd";
import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ActionBar from "../../components/modelops/ActionBar";
import StatusTag from "../../components/dataset/StatusTag";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import PageState from "../../components/ui/PageState";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { modelOpsApi } from "../../services/modelops";
import { presentInferenceTaskStatus } from "../../services/modelopsPresentation";
import { inferenceTaskListPath } from "../../services/modelopsUrl";
import { formatDateTime } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";
import type { InferenceTask } from "../../types/modelops";

function TaskDetailContent({ projectId, taskId }: { projectId: string; taskId: string }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => modelOpsApi.getTask(projectId, taskId, { signal }),
    [projectId, taskId],
  );
  const { data, error, loading, refreshing, stale, reload } = useRequest(load);
  const phase = resolvePagePhase({ hasData: data !== null, loading, error });

  const act = async (action: string, payload: { reason: string | null }) => {
    if (data === null) {
      return;
    }
    setBusy(true);
    try {
      await modelOpsApi.actTask(projectId, taskId, action, {
        version: data.version,
        reason: payload.reason,
      });
      reload();
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (phase === "forbidden" || phase === "not-found") {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else if (phase === "loading") {
    body = <PageState variant="loading" title="正在加载推理任务" />;
  } else if (phase === "error" || data === null) {
    body = error ? <ApiErrorState error={error} onRetry={reload} /> : null;
  } else {
    const task: InferenceTask = data;
    body = (
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {stale ? (
          <PageState
            compact
            variant="stale"
            description="最近一次刷新失败，展示的是上次成功获取的数据。"
            onRetry={reload}
          />
        ) : null}
        <Descriptions
          size="small"
          column={2}
          items={[
            {
              key: "status",
              label: "状态",
              children: (
                <StatusTag value={task.status} presentation={presentInferenceTaskStatus} />
              ),
            },
            {
              key: "model",
              label: "模型版本",
              children: <Typography.Text copyable>{task.model_version_id}</Typography.Text>,
            },
            { key: "service", label: "推理服务", children: task.service_id ?? "—" },
            { key: "dataset", label: "数据集版本", children: task.dataset_version_id ?? "—" },
            {
              key: "episodes",
              label: "Episode 数",
              children: task.episode_ids.length,
            },
            {
              key: "error",
              label: "错误",
              children:
                task.error_code || task.error_summary
                  ? `${task.error_code ?? ""} ${task.error_summary ?? ""}`
                  : "—",
            },
            { key: "started", label: "开始时间", children: formatDateTime(task.started_at) },
            { key: "finished", label: "完成时间", children: formatDateTime(task.finished_at) },
            { key: "log", label: "日志引用", span: 2, children: task.log_ref ?? "—" },
          ]}
        />
        <Divider style={{ margin: "8px 0" }} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          推理指标
        </Typography.Title>
        {Object.keys(task.metrics).length > 0 ? (
          <Descriptions
            size="small"
            column={3}
            items={Object.entries(task.metrics).map(([key, value]) => ({
              key,
              label: key,
              children: String(value),
            }))}
          />
        ) : (
          <Typography.Text type="secondary">适配器尚未回传指标。</Typography.Text>
        )}
        {task.result_ref ? (
          <>
            <Typography.Title level={5} style={{ margin: 0 }}>
              结果引用
            </Typography.Title>
            <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
              {JSON.stringify(task.result_ref, null, 2)}
            </pre>
          </>
        ) : null}
        <Divider style={{ margin: "8px 0" }} />
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            任务动作
          </Typography.Title>
          <ActionBar actions={task.allowed_actions} busy={busy} onAction={act} />
        </Space>
      </Space>
    );
  }

  return (
    <PageContainer
      title="推理任务详情"
      description="推理执行状态与结果只能通过适配器签名回调推进，页面不提供手动改写结果。"
      extra={
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(inferenceTaskListPath())}>
            返回列表
          </Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {body}
    </PageContainer>
  );
}

export default function InferenceTaskDetailPage() {
  const projectId = useProjectId();
  const { taskId } = useParams<{ taskId: string }>();
  if (!projectId || !taskId) {
    return (
      <PageContainer title="推理任务详情" description="推理任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="推理任务详情" />
      </PageContainer>
    );
  }
  return <TaskDetailContent key={`${projectId}:${taskId}`} projectId={projectId} taskId={taskId} />;
}
