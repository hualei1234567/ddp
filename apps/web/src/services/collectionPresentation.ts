import type {
  CollectionBatchStatus,
  CollectionPriority,
  CollectionTaskStatus,
  EpisodeAnnotationStatus,
  EpisodeQualityStatus,
  EpisodeStatus,
  RawAssetStatus,
  UploadSessionStatus,
  UploadVerificationStatus,
} from "../types/collection";

export interface StatusPresentation {
  label: string;
  color?: string;
  known: boolean;
}

function createStatusMap<T extends string>(
  labels: Record<T, string>,
  colors: Partial<Record<T, string>>,
): Record<string, StatusPresentation> {
  const map: Record<string, StatusPresentation> = {};
  for (const [value, label] of Object.entries(labels)) {
    map[value] = {
      label: label as string,
      color: colors[value as T],
      known: true,
    };
  }
  return map;
}

const TASK_STATUS = createStatusMap<CollectionTaskStatus>(
  {
    DRAFT: "草稿",
    PENDING_REVIEW: "待审核",
    APPROVED: "已批准",
    READY: "待分配",
    ASSIGNED: "已分配",
    IN_PROGRESS: "采集中",
    PAUSED: "已暂停",
    BLOCKED: "已阻塞",
    SUBMITTED: "已提交",
    DATA_CHECKING: "数据检查中",
    ACCEPTED: "已验收",
    REJECTED: "已驳回",
    REWORK: "返工中",
    COMPLETED: "已完成",
    CANCELLED: "已取消",
  },
  {
    IN_PROGRESS: "processing",
    PAUSED: "orange",
    BLOCKED: "red",
    ACCEPTED: "green",
    COMPLETED: "green",
    REJECTED: "red",
    REWORK: "volcano",
    CANCELLED: "default",
  },
);

const BATCH_STATUS = createStatusMap<CollectionBatchStatus>(
  {
    PLANNED: "已计划",
    READY: "待执行",
    IN_PROGRESS: "执行中",
    PAUSED: "已暂停",
    BLOCKED: "已阻塞",
    SUBMITTED: "已提交",
    ACCEPTED: "已验收",
    REJECTED: "已驳回",
    REWORK: "返工中",
    CANCELLED: "已取消",
  },
  {
    IN_PROGRESS: "processing",
    ACCEPTED: "green",
    REJECTED: "red",
    REWORK: "volcano",
    BLOCKED: "red",
  },
);

const UPLOAD_STATUS = createStatusMap<UploadSessionStatus>(
  {
    CREATED: "会话已创建",
    UPLOADING: "上传中",
    COMPLETING: "正在完成",
    COMPLETED: "已上传完成",
    ABORTED: "已中止",
    EXPIRED: "会话已过期",
    FAILED: "上传失败",
  },
  {
    COMPLETED: "green",
    FAILED: "red",
    EXPIRED: "orange",
    ABORTED: "default",
  },
);

const VERIFICATION_STATUS = createStatusMap<UploadVerificationStatus>(
  {
    PENDING: "服务端校验中",
    VERIFIED: "校验通过",
    REJECTED: "校验未通过",
  },
  {
    VERIFIED: "green",
    REJECTED: "red",
    PENDING: "processing",
  },
);

const EPISODE_STATUS = createStatusMap<EpisodeStatus>(
  {
    PENDING: "待处理",
    PROCESSING: "处理中",
    READY: "已就绪",
    QUALITY_CHECKING: "质检中",
    ACCEPTED: "已验收",
    COMPLETED: "已完成",
    ARCHIVED: "已归档",
    FAILED: "处理失败",
    REJECTED: "已驳回",
    INVALIDATED: "已失效",
  },
  {
    READY: "blue",
    ACCEPTED: "green",
    COMPLETED: "green",
    FAILED: "red",
    REJECTED: "red",
    INVALIDATED: "red",
    QUALITY_CHECKING: "processing",
  },
);

const QUALITY_STATUS = createStatusMap<EpisodeQualityStatus>(
  {
    NOT_CHECKED: "未质检",
    PASS: "通过",
    WARN: "警告",
    FAIL: "不通过",
  },
  { PASS: "green", WARN: "orange", FAIL: "red", NOT_CHECKED: "default" },
);

const ANNOTATION_STATUS = createStatusMap<EpisodeAnnotationStatus>(
  {
    NOT_STARTED: "未开始",
    IN_PROGRESS: "标注中",
    SUBMITTED: "已提交",
    APPROVED: "已通过",
    REJECTED: "已驳回",
  },
  { APPROVED: "green", REJECTED: "red", IN_PROGRESS: "processing" },
);

const PRIORITY = createStatusMap<CollectionPriority>(
  { P0: "P0", P1: "P1", P2: "P2" },
  { P0: "red", P1: "orange", P2: "blue" },
);

const RAW_ASSET_STATUS = createStatusMap<RawAssetStatus>(
  {
    VALIDATING: "校验中",
    VALID: "可用",
    QUARANTINED: "已隔离",
    REJECTED: "已拒绝",
    ARCHIVED: "已归档",
    TOMBSTONED: "已墓碑化",
  },
  {
    VALID: "green",
    VALIDATING: "processing",
    QUARANTINED: "orange",
    REJECTED: "red",
    ARCHIVED: "blue",
    TOMBSTONED: "default",
  },
);

function resolve(
  map: Record<string, StatusPresentation>,
  value: string,
): StatusPresentation {
  return (
    map[value] ?? {
      label: value || "未知",
      known: false,
    }
  );
}

export const getTaskStatus = (value: string): StatusPresentation =>
  resolve(TASK_STATUS, value);
export const getBatchStatus = (value: string): StatusPresentation =>
  resolve(BATCH_STATUS, value);
export const getUploadStatus = (value: string): StatusPresentation =>
  resolve(UPLOAD_STATUS, value);
export const getVerificationStatus = (value: string): StatusPresentation =>
  resolve(VERIFICATION_STATUS, value);
export const getEpisodeStatus = (value: string): StatusPresentation =>
  resolve(EPISODE_STATUS, value);
export const getQualityStatus = (value: string): StatusPresentation =>
  resolve(QUALITY_STATUS, value);
export const getAnnotationStatus = (value: string): StatusPresentation =>
  resolve(ANNOTATION_STATUS, value);
export const getPriority = (value: string): StatusPresentation =>
  resolve(PRIORITY, value);
export const getRawAssetStatus = (value: string): StatusPresentation =>
  resolve(RAW_ASSET_STATUS, value);

/** 状态机草案中的动作白名单；未知动作不渲染为可执行按钮。 */
export const COLLECTION_ACTION_VALUES = [
  "submit-review",
  "return-draft",
  "approve",
  "ready",
  "assign",
  "start",
  "pause",
  "resume",
  "block",
  "unblock",
  "submit",
  "accept",
  "reject",
  "rework",
  "complete",
  "cancel",
] as const;

export type CollectionAction = (typeof COLLECTION_ACTION_VALUES)[number];

const ACTION_LABELS: Record<CollectionAction, string> = {
  "submit-review": "提交审核",
  "return-draft": "退回草稿",
  approve: "审批通过",
  ready: "标记可分配",
  assign: "分配资源",
  start: "开始采集",
  pause: "暂停采集",
  resume: "恢复采集",
  block: "标记阻塞",
  unblock: "解除阻塞",
  submit: "提交任务",
  accept: "验收通过",
  reject: "驳回",
  rework: "进入返工",
  complete: "完成任务",
  cancel: "取消任务",
};

/** 状态机要求填写原因的动作；前端只做表单引导，服务端仍会校验。 */
const ACTION_REQUIRES_REASON = new Set<CollectionAction>([
  "return-draft",
  "pause",
  "resume",
  "block",
  "unblock",
  "reject",
  "rework",
  "cancel",
]);

export function isKnownCollectionAction(
  value: string,
): value is CollectionAction {
  return (COLLECTION_ACTION_VALUES as readonly string[]).includes(value);
}

export function getActionLabel(value: string): string {
  return isKnownCollectionAction(value) ? ACTION_LABELS[value] : value;
}

export function actionRequiresReason(value: string): boolean {
  return (
    isKnownCollectionAction(value) && ACTION_REQUIRES_REASON.has(value)
  );
}

/**
 * 只展示服务端下发的已知动作。缺失或含未知动作时，页面保持禁用/忽略，
 * 不根据 status 推断动作集合。
 */
export function resolveServerActions(
  allowedActions: string[] | undefined,
): { action: CollectionAction; label: string; requiresReason: boolean }[] {
  if (!allowedActions) {
    return [];
  }
  const seen = new Set<string>();
  const result: {
    action: CollectionAction;
    label: string;
    requiresReason: boolean;
  }[] = [];
  for (const action of allowedActions) {
    if (!isKnownCollectionAction(action) || seen.has(action)) {
      continue;
    }
    seen.add(action);
    result.push({
      action,
      label: ACTION_LABELS[action],
      requiresReason: ACTION_REQUIRES_REASON.has(action),
    });
  }
  return result;
}

export interface CountProgress {
  current: number;
  target: number;
  /** 目标为 0 或缺失时百分比为 null，界面显示 N/A，不伪造 0%。 */
  percent: number | null;
  text: string;
}

export function buildCountProgress(
  current: number,
  target: number,
  unit: string,
): CountProgress {
  const safeCurrent = Number.isFinite(current) ? current : 0;
  if (!Number.isFinite(target) || target <= 0) {
    return {
      current: safeCurrent,
      target,
      percent: null,
      text: `${safeCurrent} ${unit} / N/A`,
    };
  }
  const percent = Math.min(100, Math.round((safeCurrent / target) * 100));
  return {
    current: safeCurrent,
    target,
    percent,
    text: `${safeCurrent} / ${target} ${unit}`,
  };
}

/** 秒转可读时长；仅用于展示，不做业务阈值判断。 */
export function formatDurationSeconds(totalSeconds: number | null): string {
  if (
    totalSeconds === null ||
    !Number.isFinite(totalSeconds) ||
    totalSeconds < 0
  ) {
    return "—";
  }
  const seconds = Math.floor(totalSeconds % 60);
  const minutes = Math.floor((totalSeconds / 60) % 60);
  const hours = Math.floor(totalSeconds / 3600);
  const parts: string[] = [];
  if (hours > 0) {
    parts.push(`${hours} 小时`);
  }
  if (minutes > 0) {
    parts.push(`${minutes} 分`);
  }
  parts.push(`${seconds} 秒`);
  return parts.join(" ");
}

/** Episode 的纳秒字符串只做分组展示，避免 Number 精度丢失。 */
export function formatNanoseconds(value: string | null): string {
  if (!value || !/^-?\d+$/.test(value)) {
    return "—";
  }
  const negative = value.startsWith("-");
  const digits = negative ? value.slice(1) : value;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped} ns`;
}
