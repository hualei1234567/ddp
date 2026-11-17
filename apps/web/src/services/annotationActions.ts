/**
 * 标注链路动作的展示与可用性映射（纯函数，可单测）。
 *
 * 重要背景：后端标注路由正在分批补齐 `allowed_actions`——任务 / 批次 /
 * 分配 / 抽检 / 黄金集已返回，Schema 与样本集尚未。页面统一调用
 * `resolveAnnotationActions(kind, status, item.allowed_actions)`：
 * 服务端字段存在时以服务端为准，缺失时按服务端状态机
 * （`backend/app/domain/annotation/state_rules.py`）的迁移表兜底。
 *
 * 兜底映射是**临时实现**：待 Schema / 样本集也返回 `allowed_actions` 后，
 * 可删除 `STATUS_ACTIONS`，仅保留文案与「是否必须填写原因」。
 *
 * 这里只做展示与按钮渲染，不作为权限判断：非法迁移仍由服务端返回
 * 409/422，页面如实展示 `error.message`。
 */

export type AnnotationActionKind =
  | "schema"
  | "sample-set"
  | "task"
  | "batch"
  | "assignment"
  | "quality-sample"
  | "gold-set";

/** 由后台 worker / 系统触发的动作，不给人工按钮。 */
export const SYSTEM_ONLY_ACTIONS: ReadonlySet<string> = new Set([
  "system-review",
  "system-review-start",
  "system-expire",
  "system-supersede",
]);

/** 与后端 `_REASON_REQUIRED` 对齐：这些动作缺少原因时服务端返回 422。 */
const REASON_REQUIRED: Record<AnnotationActionKind, ReadonlySet<string>> = {
  schema: new Set(["return-draft", "deprecate", "revoke"]),
  "sample-set": new Set(["cancel"]),
  task: new Set([
    "return-draft",
    "pause",
    "resume",
    "block",
    "unblock",
    "reject",
    "escalate",
    "resolve-rejected",
    "rework",
    "cancel",
  ]),
  batch: new Set([
    "pause",
    "resume",
    "block",
    "unblock",
    "reject",
    "escalate",
    "resolve-rejected",
    "rework",
    "cancel",
  ]),
  assignment: new Set(["release", "revoke", "rework"]),
  "quality-sample": new Set(["fail", "escalate", "close", "cancel"]),
  "gold-set": new Set(["return-draft", "retire", "revoke"]),
};

const ACTION_LABELS: Record<string, string> = {
  "submit-review": "提交审核",
  "return-draft": "退回草稿",
  publish: "发布",
  deprecate: "废弃",
  revoke: "撤销",
  preview: "预览",
  snapshot: "快照",
  consume: "消耗",
  cancel: "取消",
  approve: "审批通过",
  ready: "标记就绪",
  assign: "指派",
  start: "开始",
  pause: "暂停",
  resume: "恢复",
  block: "阻塞",
  unblock: "解除阻塞",
  submit: "提交",
  "system-review": "系统复核",
  accept: "验收通过",
  reject: "验收驳回",
  escalate: "升级仲裁",
  "resolve-accepted": "仲裁通过",
  "resolve-rejected": "仲裁驳回",
  rework: "返工",
  "resume-work": "恢复作业",
  complete: "完成",
  archive: "归档",
  claim: "领取",
  release: "释放",
  "system-expire": "系统过期",
  sample: "执行抽样",
  pass: "通过",
  fail: "不通过",
  close: "关闭",
  activate: "启用",
  retire: "退役",
};

/**
 * 按后端状态迁移表列出的动作（已剔除系统动作）。
 *
 * 仅用于后端尚未返回 `allowed_actions` 的阶段；键与后端枚举逐一对齐。
 */
const STATUS_ACTIONS: Record<
  AnnotationActionKind,
  Record<string, readonly string[]>
> = {
  schema: {
    DRAFT: ["submit-review"],
    PENDING_REVIEW: ["return-draft", "publish"],
    PUBLISHED: ["deprecate", "revoke"],
    DEPRECATED: ["revoke"],
    REVOKED: [],
  },
  "sample-set": {
    DRAFT: ["preview", "cancel"],
    PREVIEW: ["snapshot", "cancel"],
    SNAPSHOTTED: ["consume", "cancel"],
    CONSUMED: [],
    CANCELLED: [],
  },
  task: {
    DRAFT: ["submit-review", "cancel"],
    PENDING_REVIEW: ["return-draft", "approve", "cancel"],
    APPROVED: ["ready", "cancel"],
    READY: ["assign", "cancel"],
    ASSIGNED: ["start", "cancel"],
    IN_PROGRESS: ["pause", "block", "submit", "cancel"],
    PAUSED: ["resume", "submit", "cancel"],
    BLOCKED: ["unblock", "submit", "cancel"],
    SUBMITTED: ["system-review", "cancel"],
    REVIEWING: ["accept", "reject", "escalate", "cancel"],
    ARBITRATION: ["resolve-accepted", "resolve-rejected", "cancel"],
    ACCEPTED: ["complete", "cancel"],
    REJECTED: ["rework", "cancel"],
    REWORK: ["resume-work", "cancel"],
    COMPLETED: ["archive"],
    CANCELLED: [],
    ARCHIVED: [],
  },
  batch: {
    PLANNED: ["ready", "cancel"],
    READY: ["assign", "cancel"],
    ASSIGNED: ["start", "cancel"],
    IN_PROGRESS: ["pause", "block", "submit", "cancel"],
    PAUSED: ["resume", "submit", "cancel"],
    BLOCKED: ["unblock", "submit", "cancel"],
    SUBMITTED: ["system-review", "cancel"],
    REVIEWING: ["accept", "reject", "escalate", "cancel"],
    ARBITRATION: ["resolve-accepted", "resolve-rejected", "cancel"],
    ACCEPTED: [],
    REJECTED: ["rework", "cancel"],
    REWORK: ["resume-work", "cancel"],
    CANCELLED: [],
  },
  assignment: {
    ASSIGNED: ["claim", "revoke", "system-expire"],
    CLAIMED: ["start", "release", "revoke", "system-expire"],
    IN_PROGRESS: ["submit", "revoke"],
    SUBMITTED: ["complete", "rework"],
    COMPLETED: [],
    REVOKED: [],
    EXPIRED: [],
  },
  "quality-sample": {
    PLANNED: ["sample", "cancel"],
    SAMPLED: ["assign", "cancel"],
    ASSIGNED: ["start", "cancel"],
    IN_REVIEW: ["pass", "fail", "escalate"],
    PASSED: ["close"],
    FAILED: ["close"],
    ESCALATED: ["close"],
    CLOSED: [],
    CANCELLED: [],
  },
  "gold-set": {
    DRAFT: ["submit-review", "revoke"],
    PENDING_REVIEW: ["return-draft", "activate", "revoke"],
    ACTIVE: ["retire", "revoke"],
    RETIRED: [],
    REVOKED: [],
  },
};

export interface AnnotationActionResolution {
  actions: readonly string[];
  /** `server` 表示来自服务端 `allowed_actions`；`status` 表示临时按状态推导。 */
  source: "server" | "status";
}

/**
 * 解析当前可渲染的动作。
 *
 * - `serverActions` 非 `undefined` 时以服务端为准（空数组表示确实没有动作）；
 * - 否则按状态迁移表兜底，并剔除系统动作。
 */
export function resolveAnnotationActions(
  kind: AnnotationActionKind,
  status: string | null | undefined,
  serverActions?: readonly string[] | null,
): AnnotationActionResolution {
  if (serverActions !== undefined && serverActions !== null) {
    return {
      actions: serverActions.filter((action) => !SYSTEM_ONLY_ACTIONS.has(action)),
      source: "server",
    };
  }
  if (typeof status !== "string" || status === "") {
    return { actions: [], source: "status" };
  }
  const actions = STATUS_ACTIONS[kind][status] ?? [];
  return {
    actions: actions.filter((action) => !SYSTEM_ONLY_ACTIONS.has(action)),
    source: "status",
  };
}

/** 动作中文文案；未知动作原样展示，不猜语义。 */
export function presentAnnotationAction(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** 该动作是否需要原因（服务端对缺失原因返回 422）。 */
export function requiresAnnotationReason(
  kind: AnnotationActionKind,
  action: string,
): boolean {
  return REASON_REQUIRED[kind].has(action);
}

/** 页面提示语：说明当前按钮来源，避免被误读为服务端权限结果。 */
export const ACTIONS_FALLBACK_NOTE =
  "当前响应未返回 allowed_actions（Schema / 样本集等）；按钮先按状态迁移表渲染，非法迁移仍由服务端拒绝。";
