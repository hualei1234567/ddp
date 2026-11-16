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

/*
 * 质量资源的页面挂在 `/quality/*`（导航「质量管理」）：
 * 黄金集、质检抽检、IAA 报告与提交质量检查同属质量模块，与标注作业
 * （任务 / 工作台 / 复核 / 仲裁）分开。旧的 `/annotation/*` 路径在
 * `routes.tsx` 里保留重定向，历史书签不会 404。
 */

export const buildQualityOverviewPath = (projectId: string) =>
  withProject("/quality", projectId);

export const buildAnnotationGoldSetListPath = (projectId: string) =>
  withProject("/quality/gold-sets", projectId);

export const buildAnnotationGoldSetDetailPath = (
  projectId: string,
  goldSetId: string,
) =>
  withProject(
    `/quality/gold-sets/${encodeURIComponent(goldSetId)}`,
    projectId,
  );

export const buildAnnotationIAAListPath = (
  projectId: string,
  taskId?: string,
) => withProject("/quality/iaa-reports", projectId, { task_id: taskId });

export const buildAnnotationIAADetailPath = (
  projectId: string,
  reportId: string,
) =>
  withProject(
    `/quality/iaa-reports/${encodeURIComponent(reportId)}`,
    projectId,
  );

export const buildAnnotationQualityListPath = (
  projectId: string,
  taskId?: string,
) =>
  withProject("/quality/quality-samples", projectId, {
    task_id: taskId,
  });

export const buildAnnotationQualityDetailPath = (
  projectId: string,
  qualitySampleId: string,
) =>
  withProject(
    `/quality/quality-samples/${encodeURIComponent(qualitySampleId)}`,
    projectId,
  );

/** 提交质量检查页：需要 submission_id 才能查历史与重跑。 */
export const buildQualityCheckPath = (
  projectId: string,
  submissionId?: string,
) =>
  withProject("/quality/quality-checks", projectId, {
    submission_id: submissionId,
  });

/** 从路由查询参数读取任意 ID；空字符串视为未选择。 */
export function readIdQuery(
  searchParams: URLSearchParams,
  key: string,
): string | null {
  const value = searchParams.get(key)?.trim() ?? "";
  return value === "" ? null : value;
}

/** 从路由查询参数读取任务 ID；空字符串视为未选择。 */
export function readTaskIdQuery(
  searchParams: URLSearchParams,
): string | null {
  return readIdQuery(searchParams, "task_id");
}
