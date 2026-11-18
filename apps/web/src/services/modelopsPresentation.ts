/** 阶段 5 枚举中文展示；只做展示，不推断权限或状态迁移。 */

import type { StatusPresentation } from "./datasetPresentation";

const MODEL_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  ACTIVE: { label: "已激活", color: "green" },
  DEPRECATED: { label: "已弃用", color: "orange" },
  ARCHIVED: { label: "已归档", color: "default" },
};

const MODEL_TASK_TYPE: Record<string, string> = {
  IMITATION: "模仿学习",
  REINFORCEMENT: "强化学习",
  VLM: "视觉语言模型",
  OTHER: "其他",
};

const VERSION_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  VALIDATING: { label: "校验中", color: "processing" },
  VALIDATION_FAILED: { label: "校验失败", color: "red" },
  READY: { label: "就绪", color: "blue" },
  RELEASED: { label: "已发布", color: "green" },
  DEPRECATED: { label: "已弃用", color: "orange" },
  ARCHIVED: { label: "已归档", color: "default" },
};

const EVALUATION_STATUS: Record<string, StatusPresentation> = {
  QUEUED: { label: "排队中", color: "default" },
  RUNNING: { label: "评测中", color: "processing" },
  SUCCEEDED: { label: "已完成", color: "green" },
  FAILED: { label: "失败", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
};

const VERDICT: Record<string, StatusPresentation> = {
  PASS: { label: "通过", color: "green" },
  FAIL: { label: "未通过", color: "red" },
  UNKNOWN: { label: "未知", color: "default" },
};

const SERVICE_STATUS: Record<string, StatusPresentation> = {
  PENDING: { label: "待部署", color: "default" },
  DEPLOYING: { label: "部署中", color: "processing" },
  ACTIVE: { label: "运行中", color: "green" },
  STOPPED: { label: "已停止", color: "default" },
  FAILED: { label: "部署失败", color: "red" },
  RETIRED: { label: "已退役", color: "default" },
};

const HEALTH_STATUS: Record<string, StatusPresentation> = {
  UNKNOWN: { label: "未知", color: "default" },
  HEALTHY: { label: "健康", color: "green" },
  UNHEALTHY: { label: "异常", color: "red" },
};

const TASK_STATUS: Record<string, StatusPresentation> = {
  QUEUED: { label: "排队中", color: "default" },
  RUNNING: { label: "执行中", color: "processing" },
  SUCCEEDED: { label: "成功", color: "green" },
  FAILED: { label: "失败", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
};

const FEEDBACK_STATUS: Record<string, StatusPresentation> = {
  OPEN: { label: "待处理", color: "orange" },
  TRIAGED: { label: "已分诊", color: "blue" },
  REQUIREMENT_CREATED: { label: "已转需求", color: "processing" },
  RESOLVED: { label: "已解决", color: "green" },
  DISMISSED: { label: "已忽略", color: "default" },
};

const SEVERITY: Record<string, StatusPresentation> = {
  BLOCKER: { label: "阻断", color: "red" },
  MAJOR: { label: "严重", color: "orange" },
  MINOR: { label: "轻微", color: "default" },
};

const FAILURE_CATEGORY: Record<string, string> = {
  PERCEPTION: "感知",
  PLANNING: "规划",
  CONTROL: "控制",
  LANGUAGE: "语言",
  OTHER: "其他",
};

const ISSUE_TYPE: Record<string, string> = {
  ACTION_COVERAGE: "动作覆盖不足",
  SCENE_COVERAGE: "场景覆盖不足",
  LONG_TAIL: "长尾缺失",
  VIEWPOINT: "视角缺失",
  GENERALIZATION: "泛化不足",
  LABEL_GAP: "标注缺口",
  DATA_QUALITY: "数据质量",
  OTHER: "其他",
};

const DIMENSION_TYPE: Record<string, string> = {
  SCENE: "场景",
  ACTION: "动作",
  ROBOT: "机器人",
  DEVICE: "设备",
  LABEL: "标签",
  MODALITY: "模态",
};

const REQUIREMENT_STATUS: Record<string, StatusPresentation> = {
  DRAFT: { label: "草稿", color: "default" },
  SUBMITTED: { label: "已提交", color: "blue" },
  APPROVED: { label: "已批准", color: "green" },
  PLANNED: { label: "已计划", color: "processing" },
  IN_PROGRESS: { label: "执行中", color: "processing" },
  SATISFIED: { label: "已满足", color: "blue" },
  CLOSED: { label: "已关闭", color: "default" },
  REJECTED: { label: "已驳回", color: "red" },
  CANCELLED: { label: "已取消", color: "default" },
};

const PRIORITY: Record<string, StatusPresentation> = {
  P0: { label: "P0 最高", color: "red" },
  P1: { label: "P1 高", color: "orange" },
  P2: { label: "P2 中", color: "blue" },
  P3: { label: "P3 低", color: "default" },
};

const SOURCE_TYPE: Record<string, string> = {
  MODEL_FEEDBACK: "模型反馈",
  EVALUATION: "评测结果",
  MANUAL: "人工登记",
  OPERATION: "运营提出",
};

function fallback(key: string): StatusPresentation {
  return { label: key, color: "default" };
}

function lookup(table: Record<string, StatusPresentation>, value: string): StatusPresentation {
  return table[value] ?? fallback(value);
}

export const presentModelStatus = (value: string) => lookup(MODEL_STATUS, value);
export const presentVersionStatus = (value: string) => lookup(VERSION_STATUS, value);
export const presentEvaluationStatus = (value: string) => lookup(EVALUATION_STATUS, value);
export const presentVerdict = (value: string) => lookup(VERDICT, value);
export const presentServiceStatus = (value: string) => lookup(SERVICE_STATUS, value);
export const presentHealthStatus = (value: string) => lookup(HEALTH_STATUS, value);
export const presentInferenceTaskStatus = (value: string) => lookup(TASK_STATUS, value);
export const presentFeedbackStatus = (value: string) => lookup(FEEDBACK_STATUS, value);
export const presentSeverity = (value: string) => lookup(SEVERITY, value);
export const presentRequirementStatus = (value: string) => lookup(REQUIREMENT_STATUS, value);
export const presentPriority = (value: string) => lookup(PRIORITY, value);

export function presentTaskType(value: string): string {
  return MODEL_TASK_TYPE[value] ?? value;
}

export function presentFailureCategory(value: string): string {
  return FAILURE_CATEGORY[value] ?? value;
}

export function presentIssueType(value: string): string {
  return ISSUE_TYPE[value] ?? value;
}

export function presentDimensionType(value: string): string {
  return DIMENSION_TYPE[value] ?? value;
}

export function presentSourceType(value: string): string {
  return SOURCE_TYPE[value] ?? value;
}

const ACTION_LABEL: Record<string, string> = {
  activate: "激活",
  deprecate: "弃用",
  restore: "恢复",
  archive: "归档",
  validate: "校验产物",
  publish: "发布版本",
  deploy: "部署",
  stop: "停止",
  retire: "退役",
  reset: "重置",
  cancel: "取消",
  triage: "分诊",
  dismiss: "忽略",
  "create-requirement": "转为数据需求",
  resolve: "标记已解决",
  submit: "提交",
  approve: "批准",
  reject: "驳回",
  plan: "计划",
  start: "开始",
  satisfy: "标记满足",
  close: "关闭",
  "link-retest": "关联复测",
};

export function presentAction(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

/** 危险/不可逆动作在按钮上给出视觉提示。 */
export function isDestructiveAction(action: string): boolean {
  return ["archive", "cancel", "dismiss", "reject", "retire", "deprecate"].includes(action);
}

/** 与后端审计约定：这些动作必须携带原因。 */
export function requiresReason(action: string): boolean {
  return ["archive", "cancel", "dismiss", "reject", "retire", "deprecate", "close"].includes(
    action,
  );
}
