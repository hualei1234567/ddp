/** 阶段 5 页面路由构造。 */

export function modelListPath(): string {
  return "/models";
}

export function modelDetailPath(modelId: string): string {
  return `/models/${encodeURIComponent(modelId)}`;
}

export function evaluationListPath(): string {
  return "/evaluations";
}

export function evaluationDetailPath(evaluationId: string): string {
  return `/evaluations/${encodeURIComponent(evaluationId)}`;
}

export function feedbackListPath(): string {
  return "/model-feedback";
}

export function feedbackDetailPath(feedbackId: string): string {
  return `/model-feedback/${encodeURIComponent(feedbackId)}`;
}

export function requirementListPath(): string {
  return "/data-requirements";
}

export function requirementDetailPath(requirementId: string): string {
  return `/data-requirements/${encodeURIComponent(requirementId)}`;
}

export function dataGapPath(): string {
  return "/data-gaps";
}

export function inferenceServiceListPath(): string {
  return "/inference-services";
}

export function inferenceTaskListPath(): string {
  return "/inference-tasks";
}

export function inferenceTaskDetailPath(taskId: string): string {
  return `/inference-tasks/${encodeURIComponent(taskId)}`;
}
