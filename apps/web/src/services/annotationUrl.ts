function withProject(
  path: string,
  projectId: string,
  extra?: Record<string, string | undefined>,
): string {
  const params = new URLSearchParams();
  params.set("project_id", projectId);
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value !== undefined && value !== "") {
        params.set(key, value);
      }
    }
  }
  return `${path}?${params.toString()}`;
}

export const buildAnnotationTaskListPath = (projectId: string) =>
  withProject("/annotation-tasks", projectId);

export const buildAnnotationTaskDetailPath = (
  projectId: string,
  taskId: string,
) => withProject(`/annotation-tasks/${encodeURIComponent(taskId)}`, projectId);

export const buildAnnotationTaskCreatePath = (projectId: string) =>
  withProject("/annotation-tasks/new", projectId);

export const buildAnnotationWorkbenchPath = (
  projectId: string,
  assignmentId: string,
) =>
  withProject(
    `/annotation/workbench/${encodeURIComponent(assignmentId)}`,
    projectId,
  );

export const buildAnnotationSubmissionPath = (
  projectId: string,
  submissionId: string,
) =>
  withProject(
    `/annotation/submissions/${encodeURIComponent(submissionId)}`,
    projectId,
  );

export const buildAnnotationReviewPath = (
  projectId: string,
  reviewId: string,
) => withProject(`/annotation/reviews/${encodeURIComponent(reviewId)}`, projectId);

export const buildAnnotationArbitrationPath = (
  projectId: string,
  arbitrationId: string,
) =>
  withProject(
    `/annotation/arbitrations/${encodeURIComponent(arbitrationId)}`,
    projectId,
  );

export const buildAnnotationReviewQueuePath = (
  projectId: string,
  taskId?: string,
) =>
  withProject("/annotation/reviews", projectId, { task_id: taskId });

export const buildAnnotationArbitrationQueuePath = (
  projectId: string,
  taskId?: string,
) =>
  withProject("/annotation/arbitrations", projectId, {
    task_id: taskId,
  });

export const buildAnnotationSchemaListPath = (projectId: string) =>
  withProject("/annotation/schemas", projectId);

export const buildAnnotationSchemaDetailPath = (
  projectId: string,
  schemaId: string,
) => withProject(`/annotation/schemas/${encodeURIComponent(schemaId)}`, projectId);

export const buildAnnotationSampleSetListPath = (projectId: string) =>
  withProject("/annotation/sample-sets", projectId);

export const buildAnnotationSampleSetDetailPath = (
  projectId: string,
  sampleSetId: string,
) =>
  withProject(
    `/annotation/sample-sets/${encodeURIComponent(sampleSetId)}`,
    projectId,
  );

export const buildAnnotationGoldSetListPath = (projectId: string) =>
  withProject("/annotation/gold-sets", projectId);

export const buildAnnotationGoldSetDetailPath = (
  projectId: string,
  goldSetId: string,
) =>
  withProject(
    `/annotation/gold-sets/${encodeURIComponent(goldSetId)}`,
    projectId,
  );

export const buildAnnotationIAAListPath = (
  projectId: string,
  taskId?: string,
) => withProject("/annotation/iaa-reports", projectId, { task_id: taskId });

export const buildAnnotationIAADetailPath = (
  projectId: string,
  reportId: string,
) =>
  withProject(
    `/annotation/iaa-reports/${encodeURIComponent(reportId)}`,
    projectId,
  );

export const buildAnnotationQualityListPath = (
  projectId: string,
  taskId?: string,
) =>
  withProject("/annotation/quality-samples", projectId, {
    task_id: taskId,
  });

export const buildAnnotationQualityDetailPath = (
  projectId: string,
  qualitySampleId: string,
) =>
  withProject(
    `/annotation/quality-samples/${encodeURIComponent(qualitySampleId)}`,
    projectId,
  );

/** 从路由查询参数读取任务 ID；空字符串视为未选择。 */
export function readTaskIdQuery(
  searchParams: URLSearchParams,
): string | null {
  const value = searchParams.get("task_id")?.trim() ?? "";
  return value === "" ? null : value;
}
