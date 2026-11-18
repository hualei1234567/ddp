/**
 * 标注 API 的共享选项类型与写请求头构造（由 `annotation.ts` 按资源域拆分抽出）。
 *
 * 职责：只放各域工厂都要用的东西——列表/写/创建选项类型，以及
 * If-Match 与 Idempotency-Key 的唯一构造实现，避免每个域各写一份。
 */
import type { ReviewChecklistItem, ReviewIssue } from "../types/annotation";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import type { PageRequestOptions } from "./projectScope";

export interface AnnotationListOptions extends PageRequestOptions {
  status?: string;
  /** true 仅逾期、false 仅未逾期；缺省不过滤（服务端 SQL 下推）。 */
  overdue?: boolean;
}

export interface AnnotationWriteOptions {
  /** 服务端当前 version；写动作必须带 If-Match。 */
  version: number;
  reason?: string | null;
  /** 任务 `complete` 在验收数量未达标时的豁免原因（服务端强制要求）。 */
  waiverReason?: string | null;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface AnnotationCreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface AssignmentListOptions extends PageRequestOptions {
  taskId?: string;
  batchId?: string;
  assigneeId?: string;
  status?: string;
}

/** 提交列表按 task_id 过滤，并支持游标分页（服务端默认 20、上限 100）。 */
export interface SubmissionListOptions extends PageRequestOptions {
  status?: string;
}

export type SubmissionCreateOptions = AnnotationCreateOptions;

export interface ReviewActionOptions extends AnnotationWriteOptions {
  issues?: ReviewIssue[];
  checklist?: ReviewChecklistItem[];
  reviewDurationSec?: number;
}

export interface ArbitrationCreateInput {
  submissionId: string;
  arbiterIds: string[];
  quorum: number;
  trigger: string;
  reviewId?: string | null;
}

export interface ReviewCreateInput {
  submissionId: string;
  reviewerId: string;
}

/** 详情类 GET 只需要取消信号；路径参数由调用方给出。 */
export type DetailRequestOptions = Pick<PageRequestOptions, "signal">;

/** 写动作头：乐观锁 + 幂等键，缺一不可（服务端 428/409）。 */
export function writeHeaders(
  options: AnnotationWriteOptions,
): Record<string, string> {
  return {
    "If-Match": formatIfMatch(options.version),
    "Idempotency-Key": options.idempotencyKey ?? createIdempotencyKey(),
  };
}

/** 创建类动作头：只带幂等键，资源尚无版本号。 */
export function createHeaders(
  options?: AnnotationCreateOptions,
): Record<string, string> {
  return {
    "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey(),
  };
}
