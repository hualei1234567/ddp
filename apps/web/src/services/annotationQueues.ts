import type {
  AnnotationArbitration,
  AnnotationRecord,
  AnnotationReview,
  AnnotationSubmission,
} from "../types/annotation";
import type { AnnotationApi } from "./annotation";
import { ApiError } from "./api";
import { asApiError } from "./annotationWorkbench";

export interface QueuePartialError {
  scope: string;
  error: ApiError;
}

export interface ReviewQueueItem {
  submission: AnnotationSubmission;
  reviews: AnnotationReview[];
}

export interface ArbitrationQueueItem {
  submission: AnnotationSubmission;
  arbitrations: AnnotationArbitration[];
}

export interface ReviewQueueResult {
  items: ReviewQueueItem[];
  partialErrors: QueuePartialError[];
}

export interface ArbitrationQueueResult {
  items: ArbitrationQueueItem[];
  partialErrors: QueuePartialError[];
}

const REVIEWABLE_SUBMISSION_STATUSES = new Set([
  "SUBMITTED",
  "IN_REVIEW",
  "ESCALATED",
]);

export function isReviewableStatus(status: string): boolean {
  return REVIEWABLE_SUBMISSION_STATUSES.has(status);
}

/**
 * 复核队列按任务聚合：后端没有独立的复核列表端点，因此先列任务提交，
 * 再并发读取每个提交的复核记录。单个提交失败只影响该行并标记 Partial。
 */
export async function loadReviewQueue(
  api: AnnotationApi,
  projectId: string,
  taskId: string,
  signal: AbortSignal,
): Promise<ReviewQueueResult> {
  const page = await api.listSubmissions(projectId, taskId, { signal });
  const submissions = page.items.filter((item) => item.status !== "DRAFT");
  const partialErrors: QueuePartialError[] = [];
  const items = await Promise.all(
    submissions.map(async (submission): Promise<ReviewQueueItem> => {
      try {
        const reviews = await api.listSubmissionReviews(
          projectId,
          submission.submission_id,
          { signal },
        );
        return { submission, reviews: reviews.items };
      } catch (caught) {
        if (!signal.aborted) {
          partialErrors.push({
            scope: `提交 ${submission.submission_id} 的复核记录`,
            error: asApiError(caught, "复核记录加载失败"),
          });
        }
        return { submission, reviews: [] };
      }
    }),
  );
  return { items, partialErrors };
}

const ARBITRATION_SUBMISSION_STATUSES = new Set([
  "ESCALATED",
  "ARBITRATING",
  "RESOLVED_APPROVED",
  "RESOLVED_REJECTED",
  "RESOLVED_REWORK",
]);

export function isArbitrationStatus(status: string): boolean {
  return ARBITRATION_SUBMISSION_STATUSES.has(status);
}

/**
 * 仲裁队列按任务聚合：只读取已升级/仲裁中/已出结论的提交，避免把普通
 * 提交误当作仲裁对象。
 */
export async function loadArbitrationQueue(
  api: AnnotationApi,
  projectId: string,
  taskId: string,
  signal: AbortSignal,
): Promise<ArbitrationQueueResult> {
  const page = await api.listSubmissions(projectId, taskId, { signal });
  const submissions = page.items.filter((item) =>
    isArbitrationStatus(item.status),
  );
  const partialErrors: QueuePartialError[] = [];
  const items = await Promise.all(
    submissions.map(async (submission): Promise<ArbitrationQueueItem> => {
      try {
        const arbitrations = await api.listSubmissionArbitrations(
          projectId,
          submission.submission_id,
          { signal },
        );
        return { submission, arbitrations: arbitrations.items };
      } catch (caught) {
        if (!signal.aborted) {
          partialErrors.push({
            scope: `提交 ${submission.submission_id} 的仲裁记录`,
            error: asApiError(caught, "仲裁记录加载失败"),
          });
        }
        return { submission, arbitrations: [] };
      }
    }),
  );
  return { items, partialErrors };
}

export interface SubmissionBundle {
  submission: AnnotationSubmission;
  reviews: AnnotationReview[];
  arbitrations: AnnotationArbitration[];
  records: AnnotationRecord[];
  partialErrors: QueuePartialError[];
}

/** 提交详情聚合；复核/仲裁/记录任一失败时保留提交主体并标记 Partial。 */
export async function loadSubmissionBundle(
  api: AnnotationApi,
  projectId: string,
  submissionId: string,
  signal: AbortSignal,
): Promise<SubmissionBundle> {
  const submission = await api.getSubmission(projectId, submissionId, {
    signal,
  });
  const partialErrors: QueuePartialError[] = [];
  const [reviews, arbitrations, records] = await Promise.all([
    api
      .listSubmissionReviews(projectId, submissionId, { signal })
      .then((page) => page.items)
      .catch((caught: unknown) => {
        if (!signal.aborted) {
          partialErrors.push({
            scope: "复核记录",
            error: asApiError(caught, "复核记录加载失败"),
          });
        }
        return [];
      }),
    api
      .listSubmissionArbitrations(projectId, submissionId, { signal })
      .then((page) => page.items)
      .catch((caught: unknown) => {
        if (!signal.aborted) {
          partialErrors.push({
            scope: "仲裁记录",
            error: asApiError(caught, "仲裁记录加载失败"),
          });
        }
        return [];
      }),
    submission.records.length > 0
      ? Promise.resolve(submission.records)
      : api
          .listSubmissionRecords(projectId, submissionId, { signal })
          .then((page) => page.items)
          .catch((caught: unknown) => {
            if (!signal.aborted) {
              partialErrors.push({
                scope: "标注记录",
                error: asApiError(caught, "标注记录加载失败"),
              });
            }
            return [];
          }),
  ]);
  return { submission, reviews, arbitrations, records, partialErrors };
}
