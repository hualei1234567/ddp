import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Typography } from "antd";
import { useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import ArbitrationQueueBody from "../../components/annotation/ArbitrationQueueBody";
import TaskScopedPrompt from "../../components/annotation/TaskScopedPrompt";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { annotationApi } from "../../services/annotation";
import { loadArbitrationQueue } from "../../services/annotationQueues";
import {
  buildAnnotationArbitrationQueuePath,
  buildAnnotationTaskDetailPath,
  readTaskIdQuery,
} from "../../services/annotationUrl";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";

function ArbitrationQueueContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      loadArbitrationQueue(annotationApi, projectId, taskId, signal),
    [projectId, taskId],
  );
  const { data, error, loading, refreshing, stale, updatedAt, reload } =
    useRequest(load);
  const phase = resolvePagePhase({
    hasData: data !== null,
    loading,
    error,
  });

  return (
    <PageContainer
      title="仲裁队列"
      description={`任务 ${taskId} · 仲裁人不得是标注人或首轮复核人；法定人数与结论由服务端校验。`}
      extra={
        <Space wrap>
          <Typography.Text type="secondary">
            更新于 {formatTimestamp(updatedAt)}
          </Typography.Text>
          <Link to={buildAnnotationTaskDetailPath(projectId, taskId)}>
            <Button icon={<ArrowLeftOutlined />}>返回任务</Button>
          </Link>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      <ArbitrationQueueBody
        projectId={projectId}
        phase={phase}
        data={data}
        error={error}
        stale={stale}
        partialErrors={data?.partialErrors ?? []}
        onRetry={reload}
      />
    </PageContainer>
  );
}

export default function AnnotationArbitrationQueuePage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const taskId = readTaskIdQuery(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="仲裁队列" description="仲裁必须按项目隔离展示。">
        <ProjectScopeRequired scope="仲裁队列" />
      </PageContainer>
    );
  }
  if (!taskId) {
    return (
      <PageContainer title="仲裁队列">
        <TaskScopedPrompt
          projectId={projectId}
          scope="仲裁队列"
          onApply={(value) =>
            navigate(buildAnnotationArbitrationQueuePath(projectId, value))
          }
        />
      </PageContainer>
    );
  }
  return (
    <ArbitrationQueueContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
