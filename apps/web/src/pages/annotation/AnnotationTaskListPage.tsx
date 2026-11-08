import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Grid, Select, Space, Typography } from "antd";
import { useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import TaskListBody from "../../components/annotation/TaskListBody";
import ApiErrorState from "../../components/ui/ApiErrorState";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { presentAnnotationStatus } from "../../services/annotationPresentation";
import { buildAnnotationTaskCreatePath } from "../../services/annotationUrl";
import { annotationApi } from "../../services/annotation";
import { resolvePagePhase } from "../../services/pagePhase";
import { ANNOTATION_TASK_STATUS_VALUES } from "../../types/annotation";

function TaskListContent({
  projectId,
  status,
}: {
  projectId: string;
  status: string | undefined;
}) {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const [searchParams] = useSearchParams();
  const loadTasks = useCallback(
    (options: CursorPageRequestOptions) =>
      annotationApi.listTasks(projectId, {
        cursor: options.cursor,
        signal: options.signal,
        status,
      }),
    [projectId, status],
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
  } = useCursorPage(loadTasks, (task) => task.task_id);
  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });
  const updateStatus = (value: string | undefined) => {
    const params = new URLSearchParams(searchParams);
    if (value) {
      params.set("status", value);
    } else {
      params.delete("status");
    }
    const query = params.toString();
    navigate(
      {
        pathname: "/annotation-tasks",
        search: query ? `?${query}` : "",
      },
      { replace: true },
    );
  };

  return (
    <PageContainer
      title="标注任务"
      description="阶段 3 标注任务、批次与分配。状态、动作与权限以服务端为准；本页不使用 Mock 任务。"
      filters={
        <Space wrap>
          <Typography.Text>状态</Typography.Text>
          <Select
            allowClear
            value={status}
            placeholder="全部状态"
            style={{ minWidth: 180 }}
            options={ANNOTATION_TASK_STATUS_VALUES.map((value) => ({
              value,
              label: presentAnnotationStatus(value).label,
            }))}
            onChange={(value) => updateStatus(value ?? undefined)}
          />
        </Space>
      }
      extra={
        <Space wrap>
          <Link to={buildAnnotationTaskCreatePath(projectId)}>
            <Button type="primary" icon={<PlusOutlined />}>
              新建任务
            </Button>
          </Link>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={reload}>
            刷新
          </Button>
        </Space>
      }
    >
      {phase === "forbidden" && error ? (
        <ApiErrorState error={error} onRetry={reload} />
      ) : (
        <TaskListBody
          projectId={projectId}
          items={items}
          loading={loading}
          refreshing={refreshing}
          error={error}
          stale={stale}
          isMobile={screens.md === false}
          hasMore={hasMore}
          loadingMore={loadingMore}
          loadMoreError={loadMoreError}
          onRetry={reload}
          onLoadMore={loadMore}
        />
      )}
    </PageContainer>
  );
}

export default function AnnotationTaskListPage() {
  const projectId = useProjectId();
  const [searchParams] = useSearchParams();
  if (!projectId) {
    return (
      <PageContainer title="标注任务" description="标注任务必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注任务列表" />
      </PageContainer>
    );
  }
  return (
    <TaskListContent
      key={`${projectId}:${searchParams.get("status") ?? ""}`}
      projectId={projectId}
      status={searchParams.get("status") ?? undefined}
    />
  );
}
