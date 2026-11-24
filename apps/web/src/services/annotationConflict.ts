/**
 * FR-019 标注冲突恢复：检测 / 差异 / 合并 / 导出（纯函数，可单测）。
 *
 * ## 边界：这里**不做**自动合并
 *
 * 审计结论是「If-Match 乐观锁 + 冲突展示已有；『复制草稿/人工合并』的完整
 * 恢复流未做」。恢复流的三件事都必须由**用户显式操作**驱动：
 *
 * | 能力 | 谁决定 | 本模块的职责 |
 * |---|---|---|
 * | 冲突检测 | 服务端 409 | 从错误体解析两侧版本，不改写任何数据 |
 * | 复制草稿 | 用户点按钮 | 把全部输入序列化成可粘贴/可下载的 JSON |
 * | 人工合并 | 用户逐项选择 | 计算差异并按用户决策合成表单值 |
 *
 * 因此本模块只提供**纯数据变换**：给定「我的值」「服务端值」「逐项选择」，
 * 算出冲突、差异与合并结果。它既不访问网络也不读写 localStorage——
 * 持久化在 `annotationConflictDraft.ts`，交互在 `AnnotationConflictModal.tsx`。
 *
 * ## 为什么以「表单值」而不是「提交记录」为合并单位
 *
 * 工作台的可编辑状态是 `Record<episodeId, EpisodeFormValues>`（见
 * `annotationValidation.ts`），服务端记录是它的投影。以表单值合并有两个好处：
 * 1. 合并结果能直接回填表单继续编辑，不需要"反向解析记录 → 表单值"；
 * 2. 用户看到的差异与最终提交的内容是同一份数据，不会出现"合并了但提交的
 *    还是旧值"。
 */

import { ApiError } from "./api";
import type { EpisodeFormValues, DraftFieldValue } from "./annotationValidation";
import type { AnnotationRecord, AnnotationSubmission } from "../types/annotation";

/** 冲突两侧：`mine` 是本地表单值，`server` 是从服务端记录还原的表单值。 */
export interface ConflictVersions {
  /** 客户端提交时使用的版本（If-Match）。 */
  expectedVersion: number;
  /** 服务端当前版本。 */
  currentVersion: number;
}

/**
 * 冲突的完整上下文：两侧版本 + 服务端最新内容（已还原为表单值）。
 *
 * `serverValues === null` 表示"只拿到了版本号，没有拿到可对比的内容"
 * （例如服务端无记录，或盲审脱敏后标签值为空）。此时只能提示与复制草稿，
 * 不能虚构差异——页面必须显式说明差异不可用。
 *
 * 这个结构同时是**刷新后从本地快照恢复**的输入：快照里存的正是这两个版本
 * 号与服务端表单值，因此恢复路径与新冲突路径共用同一份差异/合并逻辑。
 */
export interface ConflictContext extends ConflictVersions {
  serverValues: Record<string, EpisodeFormValues> | null;
}

/** 单个标签字段的差异；`field` 用 `${episodeId}.${labelKey}` 全限定。 */
export interface LabelDiff {
  episodeId: string;
  labelKey: string;
  /** 差异种类：新增（仅服务端有）、删除（仅我有）、修改（值不同）。 */
  kind: "added" | "removed" | "changed";
  mine: DraftFieldValue | undefined;
  server: DraftFieldValue | undefined;
}

/**
 * 从 409 错误体解析两侧版本。
 *
 * 兼容三种来源，按可信度排序：
 * 1. `details.expected_version` / `details.current_version`（服务端权威）；
 * 2. 只有 `current_version` 时，expected 取调用方传入的 `sentVersion`；
 * 3. 都缺失时返回 null —— 页面退化为"冲突但版本未知"提示，不猜数字。
 */
export function parseConflictVersions(
  caught: unknown,
  sentVersion: number,
): ConflictVersions | null {
  if (!(caught instanceof ApiError) || caught.status !== 409) {
    return null;
  }
  const details = caught.details;
  const rawExpected = details?.expected_version;
  const rawCurrent = details?.current_version;
  const currentVersion =
    typeof rawCurrent === "number" && Number.isInteger(rawCurrent) ? rawCurrent : null;
  const expectedVersion =
    typeof rawExpected === "number" && Number.isInteger(rawExpected)
      ? rawExpected
      : sentVersion;
  if (currentVersion === null) {
    return null;
  }
  return { expectedVersion, currentVersion };
}

/** 服务端提交是否携带可对比的记录内容（盲审脱敏时标签值为空，无差异可言）。 */
export function hasComparableRecords(
  submission: AnnotationSubmission | null,
): boolean {
  if (submission === null || submission.records.length === 0) {
    return false;
  }
  return submission.records.some((record) => record.labels.length > 0);
}

/**
 * 把服务端记录还原成工作台表单值。
 *
 * 反向映射规则必须与 `buildSubmissionRecords` 正向映射严格对称，否则"采用
 * 服务端"会得到一个提交不上去的表单：
 * - `{start_time_ns, end_time_ns}` → `{start: "..", end: ".."}`（纳秒字符串）；
 * - 数组/布尔/数字/字符串原样保留（数字转字符串，因为表单用 Input）；
 * - JSON 对象转 JSON 文本（OBJECT_TRACK/JSON 标签在表单里是文本域）。
 *
 * 服务端标签值里可能带 `time_domain` 等表单不编辑的字段，这里只取表单认识的
 * 那两个键——多带字段会让 `buildSubmissionRecords` 忽略它们，但会让差异比较
 * 出现"看起来不一样其实一样"的噪声。
 */
export function formValuesFromRecords(
  records: readonly AnnotationRecord[],
): Record<string, EpisodeFormValues> {
  const result: Record<string, EpisodeFormValues> = {};
  for (const record of records) {
    const values: EpisodeFormValues = {};
    for (const label of record.labels) {
      const converted = formValueFromServerValue(label.value);
      if (converted !== undefined) {
        values[label.label_key] = converted;
      }
    }
    result[record.episode_id] = values;
  }
  return result;
}

function formValueFromServerValue(value: unknown): DraftFieldValue | undefined {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    // 表单里 NUMBER 标签用字符串输入；直接给数字会让 Input value 变成只读文本。
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.every((item) => typeof item === "string") ? [...value] : undefined;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const start = record.start_time_ns;
    const end = record.end_time_ns;
    if (typeof start === "number" && typeof end === "number") {
      return { start: String(start), end: String(end) };
    }
    // OBJECT_TRACK/JSON：表单用文本域编辑，必须序列化回 JSON 文本。
    return JSON.stringify(value);
  }
  return undefined;
}

/**
 * 归一化表单值以便比较：空串/空数组/null 都视为"未填"。
 *
 * 不做归一化会把 `""` 与 `undefined` 报成"修改"，让差异表布满噪声，
 * 用户根本看不出真正的语义变化。
 */
export function isBlankValue(value: DraftFieldValue | undefined): boolean {
  if (value === undefined || value === null) {
    return true;
  }
  if (typeof value === "string") {
    return value.trim() === "";
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  if (typeof value === "object") {
    return value.start.trim() === "" && value.end.trim() === "";
  }
  return false;
}

function sameValue(
  left: DraftFieldValue | undefined,
  right: DraftFieldValue | undefined,
): boolean {
  const leftBlank = isBlankValue(left);
  const rightBlank = isBlankValue(right);
  if (leftBlank || rightBlank) {
    return leftBlank && rightBlank;
  }
  return JSON.stringify(left) === JSON.stringify(right);
}

/**
 * 计算「我的版本」与「服务端最新版本」的差异。
 *
 * 只报告**真正有内容差异**的字段：
 * - `added`：服务端有、我没有（他人新增）；
 * - `removed`：我有、服务端没有（他人删除或我新增）；
 * - `changed`：两边都有但值不同。
 *
 * 输出按 Episode 与标签键稳定排序，方便在界面与测试里逐项核对；不写随机
 * 或依赖对象键顺序的结果。
 */
export function diffLabelValues(
  mine: Record<string, EpisodeFormValues>,
  server: Record<string, EpisodeFormValues>,
): LabelDiff[] {
  const episodeIds = new Set([...Object.keys(mine), ...Object.keys(server)]);
  const diffs: LabelDiff[] = [];
  for (const episodeId of [...episodeIds].sort()) {
    const mineValues = mine[episodeId] ?? {};
    const serverValues = server[episodeId] ?? {};
    const labelKeys = new Set([
      ...Object.keys(mineValues),
      ...Object.keys(serverValues),
    ]);
    for (const labelKey of [...labelKeys].sort()) {
      const myValue = mineValues[labelKey];
      const serverValue = serverValues[labelKey];
      if (sameValue(myValue, serverValue)) {
        continue;
      }
      const mineBlank = isBlankValue(myValue);
      const serverBlank = isBlankValue(serverValue);
      let kind: LabelDiff["kind"];
      if (serverBlank) {
        kind = "removed";
      } else if (mineBlank) {
        kind = "added";
      } else {
        kind = "changed";
      }
      diffs.push({
        episodeId,
        labelKey,
        kind,
        mine: myValue,
        server: serverValue,
      });
    }
  }
  return diffs;
}

/** 用户对单个差异字段的选择：保留我的，或采用服务端。 */
export type MergeChoice = "mine" | "server";

export function diffFieldKey(diff: Pick<LabelDiff, "episodeId" | "labelKey">): string {
  return `${diff.episodeId}.${diff.labelKey}`;
}

/**
 * 按用户逐项选择合成合并结果。
 *
 * 语义：**未被用户选择过的差异字段一律保留我的值**。这是刻意的
 * "默认不动用户输入"——合并弹窗要求用户显式决定，而不是默认覆盖。
 * 只有用户点「采用服务端」的字段才会被替换。
 */
export function mergeByChoice(
  mine: Record<string, EpisodeFormValues>,
  server: Record<string, EpisodeFormValues>,
  choices: Readonly<Record<string, MergeChoice>>,
): Record<string, EpisodeFormValues> {
  const merged: Record<string, EpisodeFormValues> = {};
  for (const [episodeId, values] of Object.entries(mine)) {
    merged[episodeId] = { ...values };
  }
  for (const diff of diffLabelValues(mine, server)) {
    const choice = choices[diffFieldKey(diff)];
    if (choice !== "server") {
      continue;
    }
    const target = merged[diff.episodeId] ?? {};
    const serverValue = server[diff.episodeId]?.[diff.labelKey];
    if (isBlankValue(serverValue)) {
      // 服务端为空 = 采用服务端的"删除"：清空该字段而不是留我的旧值。
      // 统一走 delete（而不是写 null）——各标签类型的空值表示不同
      // （BOOLEAN 是 null、时间片段是空对象、文本是空串），
      // 让表单自己用 Schema 默认值渲染最可靠。
      delete target[diff.labelKey];
    } else {
      target[diff.labelKey] = serverValue as DraftFieldValue;
    }
    merged[diff.episodeId] = target;
  }
  return merged;
}

/**
 * 由服务端最新提交构造冲突上下文。
 *
 * 单独收敛成一个入口，是因为「差异表」与「合并基准」必须来自同一次换算：
 * 两处各调一次 `formValuesFromRecords` 虽然结果相同，但一旦有人只改其中
 * 一处（例如新增标签类型的反向映射），差异表与合并结果就会悄悄分叉。
 */
export function buildConflictContext(
  versions: ConflictVersions,
  serverSubmission: AnnotationSubmission | null,
): ConflictContext {
  if (serverSubmission === null || !hasComparableRecords(serverSubmission)) {
    return { ...versions, serverValues: null };
  }
  return {
    ...versions,
    serverValues: formValuesFromRecords(serverSubmission.records),
  };
}

/** 差异条数与分类计数，供弹窗标题与确认按钮文案使用。 */
export interface DiffSummary {
  total: number;
  added: number;
  removed: number;
  changed: number;
}

export function summarizeDiff(diffs: readonly LabelDiff[]): DiffSummary {
  const summary: DiffSummary = { total: diffs.length, added: 0, removed: 0, changed: 0 };
  for (const diff of diffs) {
    summary[diff.kind] += 1;
  }
  return summary;
}

/** 导出载荷：复制草稿与下载 JSON 共用同一份结构，保证两条路径内容一致。 */
export interface ConflictDraftExport {
  /** 导出格式版本，便于未来变更时识别旧文件。 */
  format: "ddp.annotation.conflict.v1";
  projectId: string;
  assignmentId: string;
  taskCode: string;
  /** 我方提交基准版本与服务端当前版本。 */
  expectedVersion: number;
  currentVersion: number;
  exportedAt: string;
  /** 我的完整输入（含未通过校验的必填缺失项——"不丢数据"是硬要求）。 */
  mine: Record<string, EpisodeFormValues>;
  /** 服务端最新提交快照；不可比时为 null，不伪造空记录。 */
  server: Record<string, EpisodeFormValues> | null;
  /** 差异清单，线下比对时不必自己再算一遍。 */
  diffs: LabelDiff[];
}

/**
 * 构造可复制/可下载的草稿导出。
 *
 * 关键约束：**必须包含全部用户输入**，包括未填的必填项与当前校验不通过的
 * 字段。冲突恢复的意义就是"服务端没接受我的内容"，如果导出时再按校验过滤，
 * 用户手上就真的没有自己的输入了。
 */
export function buildConflictDraftExport(params: {
  projectId: string;
  assignmentId: string;
  taskCode: string;
  context: ConflictContext;
  mine: Record<string, EpisodeFormValues>;
  now?: Date;
}): ConflictDraftExport {
  const { projectId, assignmentId, taskCode, context, mine } = params;
  const server = context.serverValues;
  return {
    format: "ddp.annotation.conflict.v1",
    projectId,
    assignmentId,
    taskCode,
    expectedVersion: context.expectedVersion,
    currentVersion: context.currentVersion,
    exportedAt: (params.now ?? new Date()).toISOString(),
    mine,
    server,
    diffs: server === null ? [] : diffLabelValues(mine, server),
  };
}

/** 导出为带缩进的 JSON 文本：粘贴到工单/IM 里人也能读。 */
export function serializeConflictDraft(draft: ConflictDraftExport): string {
  return JSON.stringify(draft, null, 2);
}

/** 下载文件名：带任务号与时间戳，避免多次导出互相覆盖。 */
export function conflictDraftFileName(draft: ConflictDraftExport): string {
  const stamp = draft.exportedAt.replace(/[:.]/g, "-");
  const safeTask = draft.taskCode.replace(/[^A-Za-z0-9._-]+/g, "_") || "task";
  return `annotation-conflict-${safeTask}-${stamp}.json`;
}

/**
 * 复制到剪贴板；成功后返回 true，不可用（非安全上下文/被拒绝）返回 false。
 *
 * 复用 `IdentifierValue` 的降级策略（`document.execCommand("copy")`），因为
 * 内网 http 部署下 `navigator.clipboard` 通常不可用。调用方在 false 时必须
 * 回退到「下载 JSON」或展示可选中的文本框，不能静默失败。
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== "undefined" &&
      typeof navigator.clipboard?.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到 execCommand 兜底；两条路径都失败才算失败。
  }
  try {
    if (typeof document === "undefined") {
      return false;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(area);
    return copied;
  } catch {
    return false;
  }
}

/** 触发浏览器下载 JSON；供剪贴板不可用时的等价出口。 */
export function downloadTextFile(fileName: string, text: string): void {
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * 冲突恢复后"重新提交"要走哪条路——由服务端当前状态决定，不由前端猜。
 *
 * 状态机（`backend/app/domain/annotation/state_rules.py`）对提交的约束：
 * - `DRAFT` 只能 `submit` / `withdraw`；
 * - 新版本（`create_draft`）只允许替代 `REWORK_REQUIRED` / `APPROVED` /
 *   `WITHDRAWN` 这三个状态（`_SUPERSEDABLE`）；
 * - 已 `SUBMITTED` / `IN_REVIEW` / … 时既不能再提交也不能建新版本。
 *
 * 所以冲突后的"用最新版本重提交"有三种真实结局，必须如实区分——把
 * `blocked` 渲染成"已提交成功"就是本节明令禁止的粉饰。
 */
export type ConflictResubmitPlan =
  | { kind: "retry-action"; submissionId: string; version: number }
  | { kind: "create-new-version"; reason: string }
  | { kind: "blocked"; reason: string };

/** 允许被新版本替代的提交状态（与后端 `_SUPERSEDABLE` 对齐）。 */
const SUPERSEDABLE_STATUSES = new Set(["REWORK_REQUIRED", "APPROVED", "WITHDRAWN"]);

/**
 * 依据服务端最新提交决定恢复动作。
 *
 * - 最新提交仍是 `DRAFT`：它只是被别人推进了 version（例如另一标签页撤回又
 *   重建），直接用最新 version 重放 `submit`；
 * - 最新提交处于可替代状态：建一个新版本携带合并后的记录再提交；
 * - 其它（SUBMITTED/IN_REVIEW/…）：流程不允许，明确返回 blocked。
 *
 * `latest === null`（服务端已没有任何提交）也按"可建新版本"处理：此时
 * `create_draft` 是唯一合法入口。
 */
export function planConflictResubmission(
  latest: { submission_id?: string; status?: string; version?: number } | null,
): ConflictResubmitPlan {
  if (latest === null) {
    return {
      kind: "create-new-version",
      reason: "服务端当前没有提交版本，将基于合并结果创建新版本。",
    };
  }
  const status = latest.status ?? "";
  if (status === "DRAFT") {
    if (typeof latest.submission_id !== "string" || typeof latest.version !== "number") {
      return { kind: "blocked", reason: "服务端草稿缺少版本信息，请刷新后重试。" };
    }
    return {
      kind: "retry-action",
      submissionId: latest.submission_id,
      version: latest.version,
    };
  }
  if (SUPERSEDABLE_STATUSES.has(status)) {
    return {
      kind: "create-new-version",
      reason: `服务端最新版本状态为 ${status}，可基于合并结果创建新版本。`,
    };
  }
  return {
    kind: "blocked",
    reason: `服务端最新版本状态为 ${status}，流程不允许再次提交；合并结果已保留在本地草稿与导出文件里，请等复核/返工结论或联系标注管理员。`,
  };
}
