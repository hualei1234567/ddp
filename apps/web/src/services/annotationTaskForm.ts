/**
 * 标注任务创建表单的纯转换逻辑（不依赖 React，可单测）。
 *
 * 双标策略复用任务既有的 `iaa_policy` 契约字段，由服务端
 * `ReviewPolicy` 统一校验，本页不新增接口、不新增字段：
 * - `review_mode`：`SINGLE`（默认）或 `DOUBLE`；
 * - `agreement_threshold`：一致性阈值，契约要求 0–1；
 * - `auto_approve`：达标后自动通过，默认关闭，且仅项目负责人可开启。
 *
 * 界面按百分数（0–100）输入阈值，提交前换算为 0–1；留空则不提交，
 * 交给服务端默认策略，避免前端复制后端默认阈值。
 */

import {
  REVIEW_MODE_VALUES,
  TASK_PRIORITY_VALUES,
  type AnnotationTaskCreateInput,
  type ReviewMode,
  type TaskPriority,
} from "../types/annotation";
import type { ProjectRole } from "../types/project";

export interface AnnotationTaskFormValues {
  code: string;
  name: string;
  description?: string;
  sample_set_id: string;
  schema_id: string;
  target_item_count: number;
  target_episode_count: number;
  priority: TaskPriority;
  assignment_mode: string;
  due_at?: string;
  review_mode: ReviewMode;
  /** 界面百分数；换算与校验在 `buildReviewPolicy` 内完成。 */
  agreement_threshold_percent?: number | null;
  auto_approve?: boolean;
}

export interface AnnotationTaskFormOptions {
  schemaVersion: number | null;
  /** 由项目响应 `membership_role` 得出的按钮提示；服务端仍是权威。 */
  canAutoApprove: boolean;
}

/**
 * 开启自动通过的门槛与服务端一致：项目负责人或平台管理员
 * （平台管理员的 `membership_role` 为 null）。
 * 这里只决定前端是否展示开关，不做授权判断。
 */
export function canEnableAutoApprove(role: ProjectRole | null): boolean {
  return role === null || role === "owner";
}

function requireText(
  value: string | undefined,
  label: string,
  max: number,
): string {
  const text = (value ?? "").trim();
  if (text === "") {
    throw new Error(`请填写${label}`);
  }
  if (text.length > max) {
    throw new Error(`${label}最多 ${max} 字符`);
  }
  return text;
}

function optionalText(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

function requireIdentifier(value: string | undefined, label: string): string {
  const text = (value ?? "").trim();
  if (text === "") {
    throw new Error(`请选择${label}`);
  }
  return text;
}

function requirePositiveInteger(
  value: number | undefined,
  label: string,
): number {
  if (value === undefined || !Number.isInteger(value) || value < 1) {
    throw new Error(`${label}必须是不小于 1 的整数`);
  }
  return value;
}

/** 空白返回 null；非法格式直接拒绝，避免静默丢弃用户输入。 */
function toIsoOrNull(value: string | undefined): string | null {
  const text = value?.trim() ?? "";
  if (text === "") {
    return null;
  }
  const parsed = Date.parse(text);
  if (Number.isNaN(parsed)) {
    throw new Error("截止时间不是合法的日期时间");
  }
  return new Date(parsed).toISOString();
}

function percentToFraction(value: number | null | undefined): number | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  if (!Number.isFinite(value) || value < 0 || value > 100) {
    throw new Error("一致性阈值必须在 0 到 100 之间（百分数）");
  }
  // 保留 4 位小数，避免 0.8 之类的换算出现浮点尾巴。
  return Number((value / 100).toFixed(4));
}

/**
 * 组装 `iaa_policy`。单人任务不写阈值与自动通过；双标任务阈值可留空，
 * 自动通过必须显式勾选且调用方已具备权限提示。
 */
export function buildReviewPolicy(
  values: Pick<
    AnnotationTaskFormValues,
    "review_mode" | "agreement_threshold_percent" | "auto_approve"
  >,
  canAutoApprove: boolean,
): Record<string, unknown> {
  if (!REVIEW_MODE_VALUES.includes(values.review_mode)) {
    throw new Error("复核模式必须是 SINGLE 或 DOUBLE");
  }
  const policy: Record<string, unknown> = { review_mode: values.review_mode };
  if (values.review_mode === "DOUBLE") {
    const threshold = percentToFraction(values.agreement_threshold_percent);
    if (threshold !== undefined) {
      policy.agreement_threshold = threshold;
    }
    if (values.auto_approve) {
      if (!canAutoApprove) {
        throw new Error("只有项目负责人可以开启双标自动通过");
      }
      policy.auto_approve = true;
    }
    return policy;
  }
  if (values.auto_approve) {
    throw new Error("自动通过仅适用于双标（DOUBLE）任务");
  }
  return policy;
}

export function buildAnnotationTaskCreateInput(
  values: AnnotationTaskFormValues,
  options: AnnotationTaskFormOptions,
): AnnotationTaskCreateInput {
  if (!TASK_PRIORITY_VALUES.includes(values.priority)) {
    throw new Error("优先级必须是 P0 / P1 / P2 / P3");
  }
  return {
    code: requireText(values.code, "任务编码", 64),
    name: requireText(values.name, "任务名称", 200),
    description: optionalText(values.description),
    sample_set_id: requireIdentifier(values.sample_set_id, "样本集"),
    schema_id: requireIdentifier(values.schema_id, "已发布 Schema"),
    schema_version: options.schemaVersion,
    target_item_count: requirePositiveInteger(
      values.target_item_count,
      "目标项数",
    ),
    target_episode_count: requirePositiveInteger(
      values.target_episode_count,
      "目标 Episode 数",
    ),
    due_at: toIsoOrNull(values.due_at),
    priority: values.priority,
    assignment_mode: requireText(values.assignment_mode, "分配模式", 16),
    iaa_policy: buildReviewPolicy(values, options.canAutoApprove),
  };
}
