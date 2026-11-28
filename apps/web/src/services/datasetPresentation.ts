/** 阶段 4 状态与枚举的中文展示；只做展示，不推断权限或动作。 */

export interface StatusPresentation {
  label: string;
  color: string;
}

const DATASET_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  ACTIVE: { label: "已激活", color: "green" },
  ARCHIVED: { label: "已归档", color: "default" },
};

const VERSION_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  BUILDING: { label: "构建中", color: "processing" },
  VALIDATING: { label: "校验中", color: "processing" },
  READY: { label: "就绪", color: "blue" },
  FROZEN: { label: "已冻结", color: "green" },
  SUPERSEDED: { label: "已被替代", color: "default" },
  VALIDATION_FAILED: { label: "校验失败", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
  ARCHIVED: { label: "已归档", color: "default" },
};

const VALIDITY_STATUS: Record<string, StatusPresentation> = {
  VALID: { label: "有效", color: "green" },
  STALE: { label: "陈旧", color: "orange" },
  INVALIDATED: { label: "已失效", color: "red" },
  UNKNOWN: { label: "未知", color: "default" },
};

const LEAKAGE_STATUS: Record<string, StatusPresentation> = {
  PASS: { label: "通过", color: "green" },
  WARN: { label: "告警", color: "orange" },
  FAIL: { label: "失败", color: "red" },
  WAIVED: { label: "已豁免", color: "gold" },
  UNKNOWN: { label: "未知", color: "default" },
};

const EXPORT_STATUS: Record<string, StatusPresentation> = {
  QUEUED: { label: "排队中", color: "default" },
  RUNNING: { label: "导出中", color: "processing" },
  SUCCEEDED: { label: "成功", color: "green" },
  FAILED: { label: "失败", color: "red" },
  PARTIAL: { label: "部分成功", color: "orange" },
  CANCELLED: { label: "已取消", color: "default" },
};

const TRAINING_REQUEST_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  SUBMITTED: { label: "已提交", color: "blue" },
  PENDING_REVIEW: { label: "待审批", color: "processing" },
  APPROVED: { label: "已批准", color: "green" },
  IN_DELIVERY: { label: "交付中", color: "processing" },
  DELIVERED: { label: "已交付", color: "blue" },
  ACCEPTED: { label: "已验收", color: "green" },
  CLOSED: { label: "已关闭", color: "default" },
  REJECTED: { label: "已驳回", color: "red" },
  REVISION_REQUIRED: { label: "需修订", color: "orange" },
  BLOCKED: { label: "已阻塞", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
};

const REVISION_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  SUBMITTED: { label: "已提交", color: "blue" },
  PENDING_REVIEW: { label: "待审批", color: "processing" },
  APPROVED: { label: "已批准", color: "green" },
  REJECTED: { label: "已驳回", color: "red" },
  SUPERSEDED: { label: "已被替代", color: "default" },
};

const DELIVERY_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  READY: { label: "待交付", color: "blue" },
  DELIVERED: { label: "已交付", color: "processing" },
  ACCEPTED: { label: "已验收", color: "green" },
  REJECTED: { label: "已驳回", color: "red" },
  REWORK: { label: "返工中", color: "orange" },
  CANCELLED: { label: "已取消", color: "default" },
};

const EXTERNAL_STATUS: Record<string, StatusPresentation> = {
  SUBMITTED: { label: "已提交", color: "blue" },
  RUNNING: { label: "运行中", color: "processing" },
  SUCCEEDED: { label: "成功", color: "green" },
  FAILED: { label: "失败", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
  UNKNOWN: { label: "未知", color: "default" },
  UNAVAILABLE: { label: "平台不可达", color: "orange" },
  // N7-007：演练专用终态。必须与「成功」视觉区分，避免把演练读成真实训练。
  DRY_RUN: { label: "演练（未训练）", color: "orange" },
};

// 执行方式：MOCK / DRY_RUN / UNKNOWN 用高对比色，避免被误读成真实训练结果。
const EXECUTION_MODE: Record<string, StatusPresentation> = {
  LOCAL: { label: "本地执行（真实）", color: "cyan" },
  MOCK: { label: "模拟训练（MOCK）", color: "orange" },
  DRY_RUN: { label: "空跑（DRY_RUN）", color: "orange" },
  REMOTE: { label: "远端真实训练", color: "green" },
  UNKNOWN: { label: "执行方式未知", color: "red" },
};

const EVIDENCE_RUN_KIND: Record<string, StatusPresentation> = {
  SMOKE: { label: "SMOKE 冒烟", color: "blue" },
  REAL: { label: "REAL 正式训练", color: "green" },
};

const EVIDENCE_STATUS: Record<string, StatusPresentation> = {
  SUCCEEDED: { label: "成功", color: "green" },
  FAILED: { label: "失败", color: "red" },
};

const GRANT_STATUS: Record<string, StatusPresentation> = {
  PENDING_APPROVAL: { label: "待审批", color: "processing" },
  ACTIVE: { label: "生效中", color: "green" },
  REJECTED: { label: "已拒绝", color: "red" },
  EXPIRED: { label: "已过期", color: "default" },
  REVOKED: { label: "已撤销", color: "default" },
};

const PRIORITY: Record<string, StatusPresentation> = {
  P0: { label: "P0", color: "red" },
  P1: { label: "P1", color: "orange" },
  P2: { label: "P2", color: "blue" },
  P3: { label: "P3", color: "default" },
};

const PURPOSE: Record<string, string> = {
  TRAINING: "训练",
  EVALUATION: "评估",
  BENCHMARK: "基准",
  OTHER: "其他",
};

const USAGE_POLICY: Record<string, StatusPresentation> = {
  TRAINING_ALLOWED: { label: "允许训练", color: "green" },
  TRAINING_DENIED: { label: "禁止训练", color: "red" },
  UNKNOWN: { label: "用途未知", color: "orange" },
};

const SPLIT: Record<string, string> = {
  TRAIN: "训练",
  VALIDATION: "验证",
  TEST: "测试",
};

function present(
  mapping: Record<string, StatusPresentation>,
  value: string,
): StatusPresentation {
  return mapping[value] ?? { label: value, color: "default" };
}

export const presentDatasetStatus = (value: string) => present(DATASET_STATUS, value);
export const presentVersionStatus = (value: string) => present(VERSION_STATUS, value);
export const presentValidityStatus = (value: string) => present(VALIDITY_STATUS, value);
export const presentLeakageStatus = (value: string) => present(LEAKAGE_STATUS, value);
export const presentExportStatus = (value: string) => present(EXPORT_STATUS, value);
export const presentTrainingRequestStatus = (value: string) =>
  present(TRAINING_REQUEST_STATUS, value);
export const presentRevisionStatus = (value: string) => present(REVISION_STATUS, value);
export const presentDeliveryStatus = (value: string) => present(DELIVERY_STATUS, value);
export const presentExternalStatus = (value: string) => present(EXTERNAL_STATUS, value);
export const presentExecutionMode = (value: string) => present(EXECUTION_MODE, value);
export const presentEvidenceRunKind = (value: string) => present(EVIDENCE_RUN_KIND, value);
export const presentEvidenceStatus = (value: string) => present(EVIDENCE_STATUS, value);
export const presentGrantStatus = (value: string) => present(GRANT_STATUS, value);
export const presentPriority = (value: string) => present(PRIORITY, value);
export const presentUsagePolicy = (value: string) => present(USAGE_POLICY, value);
export const presentPurpose = (value: string): string => PURPOSE[value] ?? value;
export const presentSplit = (value: string): string => SPLIT[value] ?? value;

export const ACTION_LABELS: Record<string, string> = {
  activate: "激活",
  archive: "归档",
  restore: "恢复",
  build: "构建",
  validate: "校验",
  "return-draft": "退回草稿",
  freeze: "冻结",
  invalidate: "标记失效",
  revalidate: "确认恢复有效",
  cancel: "取消",
  approve: "批准",
  reject: "驳回",
  revoke: "撤销",
  retry: "重试",
  submit: "提交",
  block: "阻塞",
  unblock: "解除阻塞",
  close: "关闭",
  ready: "置为待交付",
  deliver: "交付",
  accept: "验收",
  rework: "返工",
  "resume-ready": "恢复待交付",
  sync: "同步状态",
};

export const presentAction = (action: string): string =>
  ACTION_LABELS[action] ?? action;
