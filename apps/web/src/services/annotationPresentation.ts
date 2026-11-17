import { ApiError } from "./api";

export type AnnotationTone = "default" | "processing" | "success" | "warning" | "error";

export interface AnnotationStatusPresentation {
  label: string;
  tone: AnnotationTone;
}

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  PENDING_REVIEW: "待审批",
  APPROVED: "已通过",
  READY: "就绪",
  ASSIGNED: "已分配",
  IN_PROGRESS: "进行中",
  PAUSED: "已暂停",
  BLOCKED: "已阻塞",
  SUBMITTED: "已提交",
  REVIEWING: "复核中",
  ARBITRATION: "仲裁中",
  ACCEPTED: "已验收",
  REJECTED: "已驳回",
  REWORK: "返工中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  ARCHIVED: "已归档",
  CLAIMED: "已领取",
  REVOKED: "已撤销",
  EXPIRED: "已过期",
  PUBLISHED: "已发布",
  DEPRECATED: "已废弃",
  PREVIEW: "已预览",
  SNAPSHOTTED: "已快照",
  CONSUMED: "已消费",
  IN_REVIEW: "复核中",
  ESCALATED: "已升级",
  ARBITRATING: "仲裁中",
  RESOLVED_APPROVED: "仲裁通过",
  RESOLVED_REJECTED: "仲裁驳回",
  RESOLVED_REWORK: "仲裁返工",
  REWORK_REQUIRED: "需返工",
  WITHDRAWN: "已撤回",
  SUPERSEDED: "已被替代",
  INVALIDATED: "已失效",
  PENDING: "待处理",
  IN_ARBITRATION: "仲裁进行中",
  ACTIVE: "已启用",
  RETIRED: "已退役",
  PLANNED: "已计划",
  SAMPLED: "已抽样",
  PASSED: "已通过",
  FAILED: "未通过",
  CLOSED: "已关闭",
  QUEUED: "排队中",
  RUNNING: "计算中",
  SUCCEEDED: "计算成功",
  INSUFFICIENT_DATA: "样本不足",
  NORMAL: "正常",
  AT_CAPACITY: "已达容量",
  OVER_CAPACITY: "超容量",
  STALE: "陈旧",
  PARTIAL: "部分缺失",
  UNKNOWN: "未知",
  NONE: "无",
  "N/A": "不适用",
  OK: "有效",
};

const ERROR_STATUS = new Set([
  "BLOCKED",
  "REJECTED",
  "FAILED",
  "REVOKED",
  "EXPIRED",
  "CANCELLED",
  "INVALIDATED",
  "OVER_CAPACITY",
  "RESOLVED_REJECTED",
  "REWORK",
  "REWORK_REQUIRED",
]);
const SUCCESS_STATUS = new Set([
  "APPROVED",
  "ACCEPTED",
  "COMPLETED",
  "PUBLISHED",
  "SNAPSHOTTED",
  "PASSED",
  "SUCCEEDED",
  "ACTIVE",
  "RESOLVED_APPROVED",
]);
const WARNING_STATUS = new Set([
  "PENDING_REVIEW",
  "PAUSED",
  "REVIEWING",
  "ARBITRATION",
  "IN_REVIEW",
  "ESCALATED",
  "ARBITRATING",
  "RESOLVED_REWORK",
  "INSUFFICIENT_DATA",
  "IN_REVIEW",
  "IN_ARBITRATION",
  "AT_CAPACITY",
  "STALE",
  "PARTIAL",
]);

export function presentAnnotationStatus(
  value: string | null | undefined,
): AnnotationStatusPresentation {
  if (typeof value !== "string" || value === "") {
    return { label: "未知", tone: "default" };
  }
  const label = STATUS_LABELS[value] ?? value;
  if (ERROR_STATUS.has(value)) {
    return { label, tone: "error" };
  }
  if (SUCCESS_STATUS.has(value)) {
    return { label, tone: "success" };
  }
  if (WARNING_STATUS.has(value)) {
    return { label, tone: "warning" };
  }
  if (
    value === "DRAFT" ||
    value === "READY" ||
    value === "ASSIGNED" ||
    value === "CLAIMED" ||
    value === "PLANNED" ||
    value === "SAMPLED" ||
    value === "QUEUED" ||
    value === "RUNNING" ||
    value === "IN_PROGRESS"
  ) {
    return { label, tone: "processing" };
  }
  return { label, tone: "default" };
}

/** 职责分离与黄金集访问相关错误码；页面需要给出明确说明而非泛化 403。 */
export const SEPARATION_ERROR_CODES = new Set([
  "annotation_review_self_forbidden",
  "annotation_arbiter_conflict",
  "annotation_assignment_not_owned",
  "gold_set_access_denied",
  "quality_sample_self_review_forbidden",
  "iaa_source_forbidden",
]);

export function isSeparationError(error: ApiError): boolean {
  return error.status === 403 && SEPARATION_ERROR_CODES.has(error.code ?? "");
}

export interface AnnotationFeedback {
  kind: "success" | "warning" | "error";
  title: string;
  description: string;
}

function detail(error: ApiError): string {
  const code = error.code ?? "unknown";
  const requestId = error.requestId ?? "—";
  // 专项文案之外始终附带服务端 error.message，页面不需要再猜失败原因。
  return `服务端消息：${error.message}；错误码：${code}；request_id：${requestId}。`;
}

export function describeAnnotationActionError(
  caught: unknown,
): AnnotationFeedback {
  const error =
    caught instanceof ApiError
      ? caught
      : new ApiError({
          kind: "network",
          message: "请求失败，请检查网络或服务状态。",
          cause: caught,
        });
  if (error.code === "gold_set_access_denied") {
    return {
      kind: "error",
      title: "黄金集期望答案不可见",
      description: `当前账号无权查看该黄金集答案；仅授权角色可访问，服务端已拒绝并记录审计。${detail(error)}`,
    };
  }
  if (error.code === "annotation_review_self_forbidden") {
    return {
      kind: "error",
      title: "职责分离：不能复核自己的提交",
      description: `复核人必须是标注人之外的项目成员。${detail(error)}`,
    };
  }
  if (error.code === "annotation_arbiter_conflict") {
    return {
      kind: "error",
      title: "职责分离：仲裁人存在利益冲突",
      description: `仲裁人不得是标注人或首轮复核人，且操作者必须是该仲裁的仲裁人。${detail(error)}`,
    };
  }
  if (
    error.code === "annotation_assignment_not_owned" ||
    error.code === "quality_sample_self_review_forbidden"
  ) {
    return {
      kind: "error",
      title: "职责分离：操作人与标注任务不匹配",
      description: `该操作只能由对应人员执行。${detail(error)}`,
    };
  }
  if (error.isForbidden) {
    return {
      kind: "error",
      title: "无权执行该操作",
      description: `权限以服务端校验为准。${detail(error)}`,
    };
  }
  if (
    error.isConflict ||
    error.code === "precondition_required" ||
    error.code === "state_transition_invalid" ||
    error.code === "annotation_submission_not_editable" ||
    error.code === "annotation_review_not_pending" ||
    error.code === "annotation_arbitration_not_pending" ||
    error.code === "annotation_arbitration_quorum_not_met"
  ) {
    return {
      kind: "warning",
      title: "资源已变化或当前状态不允许该操作",
      description: `本地输入已保留；请刷新后基于最新版本重试，页面不会覆盖他人修改。${detail(error)}`,
    };
  }
  if (error.code === "annotation_submission_incomplete" || error.status === 422) {
    return {
      kind: "error",
      title: "提交内容未通过校验",
      description: `请检查必填标签、类型、范围与时间片段。${detail(error)}`,
    };
  }
  return {
    kind: "error",
    title: "操作失败",
    description: `${error.message}；${detail(error)}`,
  };
}

export function annotationSuccess(message: string): AnnotationFeedback {
  return {
    kind: "success",
    title: "操作已提交",
    description: `${message} 正在刷新服务端最新状态。`,
  };
}

/** 任务进度来自服务端 `progress` 对象；缺失字段显示 N/A，不按 0 猜测。 */
export function presentProgress(
  progress: Record<string, unknown>,
  key: string,
): string {
  const value = progress[key];
  if (value === undefined || value === null) {
    return "N/A";
  }
  return typeof value === "number" ? String(value) : String(value);
}

export function presentMissingSemantics(value: unknown): string {
  return typeof value === "string" && value !== "" ? value : "N/A";
}
