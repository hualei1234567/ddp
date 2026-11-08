import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Space, Typography } from "antd";
import { useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import ReviewQueueBody from "../../components/annotation/ReviewQueueBody";
import TaskScopedPrompt from "../../components/annotation/TaskScopedPrompt";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useProjectId } from "../../hooks/useProjectId";
import { useRequest } from "../../hooks/useRequest";
import { loadReviewQueue } from "../../services/annotationQueues";
import {
  buildAnnotationReviewQueuePath,
  buildAnnotationTaskDetailPath,
  readTaskIdQuery,
} from "../../services/annotationUrl";
import { annotationApi } from "../../services/annotation";
import { formatTimestamp } from "../../services/format";
import { resolvePagePhase } from "../../services/pagePhase";

function ReviewQueueContent({
  projectId,
  taskId,
}: {
  projectId: string;
  taskId: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) =>
      loadReviewQueue(annotationApi, projectId, taskId, signal),
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
      title="复核队列"
      description={`任务 ${taskId} · 复核人不得是标注人；权限、状态与职责分离以服务端为准。`}
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
      <ReviewQueueBody
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

export default function AnnotationReviewQueuePage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const taskId = readTaskIdQuery(searchParams);

  if (!projectId) {
    return (
      <PageContainer title="复核队列" description="复核必须按项目隔离展示。">
        <ProjectScopeRequired scope="复核队列" />
      </PageContainer>
    );
  }
  if (!taskId) {
    return (
      <PageContainer title="复核队列">
        <TaskScopedPrompt
          projectId={projectId}
          scope="复核队列"
          onApply={(value) =>
            navigate(buildAnnotationReviewQueuePath(projectId, value))
          }
        />
      </PageContainer>
    );
  }
  return (
    <ReviewQueueContent
      key={`${projectId}:${taskId}`}
      projectId={projectId}
      taskId={taskId}
    />
  );
}
