import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import TaskScopedPrompt from "../../components/annotation/TaskScopedPrompt";
import PageContainer from "../../components/ui/PageContainer";
import ProjectScopeRequired from "../../components/ui/ProjectScopeRequired";
import { useCursorPage } from "../../hooks/useCursorPage";
import type { CursorPageRequestOptions } from "../../hooks/useCursorPage";
import { useProjectId } from "../../hooks/useProjectId";
import { ApiError } from "../../services/api";
import { annotationApi } from "../../services/annotation";
import { buildQualityCheckPath, readIdQuery } from "../../services/annotationUrl";
import { resolvePagePhase } from "../../services/pagePhase";
import type { QualityCheckRun } from "../../types/quality";
import QualityCheckBody from "./QualityCheckBody";

function toApiError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({
        kind: "network",
        message: "质量检查请求失败，请检查网络或服务状态。",
        cause: caught,
      });
}

function QualityCheckContent({
  projectId,
  submissionId,
}: {
  projectId: string;
  submissionId: string;
}) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunError, setRerunError] = useState<ApiError | null>(null);

  const load = (options: CursorPageRequestOptions) =>
    annotationApi.listQualityChecks(projectId, submissionId, {
      cursor: options.cursor,
      signal: options.signal,
    });
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
  } = useCursorPage<QualityCheckRun>(load, (run) => run.run_id);

  const phase = resolvePagePhase({
    hasData: items.length > 0,
    loading,
    error,
  });

  const rerun = async () => {
    setRerunning(true);
    setRerunError(null);
    try {
      const run = await annotationApi.runQualityCheck(projectId, submissionId);
      setSelectedRunId(run.run_id);
      reload();
    } catch (caught) {
      setRerunError(toApiError(caught));
    } finally {
      setRerunning(false);
    }
  };

  return (
    <QualityCheckBody
      submissionId={submissionId}
      phase={phase}
      runs={items}
      selectedRunId={selectedRunId}
      onSelectRun={setSelectedRunId}
      loading={loading}
      refreshing={refreshing}
      loadingMore={loadingMore}
      hasMore={hasMore}
      stale={stale}
      error={error}
      loadMoreError={loadMoreError}
      rerunning={rerunning}
      rerunError={rerunError}
      onReload={reload}
      onLoadMore={loadMore}
      onRerun={() => void rerun()}
    />
  );
}

export default function QualityCheckPage() {
  const projectId = useProjectId();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const submissionId = readIdQuery(searchParams, "submission_id");

  if (!projectId) {
    return (
      <PageContainer title="标注质量检查" description="质量检查必须按项目隔离展示。">
        <ProjectScopeRequired scope="标注质量检查" />
      </PageContainer>
    );
  }
  if (!submissionId) {
    return (
      <PageContainer title="标注质量检查">
        <TaskScopedPrompt
          projectId={projectId}
          scope="质量检查"
          title="请选择提交以查看质量检查"
          placeholder="标注提交 UUID"
          hint="质量检查按提交聚合，需要 URL 参数 submission_id 或在此输入提交 ID。"
          showTaskListLink={false}
          onApply={(value) => navigate(buildQualityCheckPath(projectId, value))}
        />
      </PageContainer>
    );
  }
  return (
    <QualityCheckContent
      key={`${projectId}:${submissionId}`}
      projectId={projectId}
      submissionId={submissionId}
    />
  );
}
