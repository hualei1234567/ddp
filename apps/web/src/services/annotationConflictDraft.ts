/**
 * FR-019 冲突恢复的本地持久化：未合并的冲突快照必须能跨刷新恢复。
 *
 * ## 为什么不能只靠 `annotationDraft`
 *
 * 工作台常规自动保存（`annotationDraft.ts`）保存的是**当前表单值**，但它有
 * 两个盲区，正好覆盖冲突场景：
 * 1. 提交失败后页面会 `flushDraft()` 再 `reload()`；刷新会重新 hydrate 表单，
 *    但"这是一次未合并的冲突"这个事实本身没有任何地方记录；
 * 2. 用户关掉冲突弹窗后继续编辑，表单值会被新输入覆盖——冲突发生时那份
 *    「我的版本」就此消失，与服务端最新版本的差异再也无法展示。
 *
 * 所以冲突恢复需要一份**独立的、不可被普通自动保存覆盖的**快照：
 * - 键包含项目 + 分配 + 服务端版本，同一冲突重复出现时覆盖同一格；
 * - 只有在「重新提交成功」或用户显式丢弃时才删除；
 * - 解析失败/结构不符时返回 null 并保留原始串（不静默清空用户数据）。
 *
 * 存储不可用（隐私模式、配额满）时所有写操作返回 false，由页面提示
 * "本次冲突无法跨刷新恢复，请先复制草稿"——不假装成功。
 */

import type { EpisodeFormValues } from "./annotationValidation";

export const CONFLICT_SNAPSHOT_VERSION = 1;

/** 未合并冲突快照：恢复流的完整输入，不含任何派生的展示状态。 */
export interface ConflictSnapshot {
  version: typeof CONFLICT_SNAPSHOT_VERSION;
  projectId: string;
  assignmentId: string;
  taskCode: string;
  /** 我方基准版本（提交时用的 If-Match）。 */
  expectedVersion: number;
  /** 服务端当前版本（冲突发生时的权威值）。 */
  currentVersion: number;
  /** 冲突发生时我的全部表单值。 */
  mine: Record<string, EpisodeFormValues>;
  /** 服务端最新提交的记录（已还原为表单值）；不可比时为 null。 */
  server: Record<string, EpisodeFormValues> | null;
  /** 用户已做出的逐项合并选择，刷新后继续保留。 */
  choices: Record<string, "mine" | "server">;
  updatedAt: number;
}

export interface ConflictStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/**
 * 键按「项目 + 分配」隔离，**不包含服务端版本**。
 *
 * 这是刻意选择：刷新后页面还没加载完工作台、也就不知道服务端当前版本，
 * 键里带版本号会导致"已知密钥才能读快照、读快照才知道冲突"的死锁。
 * 一个分配同时只可能有活跃冲突（提交成功即清空），因此单格足矣；
 * 新冲突覆盖旧冲突时，旧的「我的版本」已经由新一次提交携带。
 */
export function conflictSnapshotKey(
  projectId: string,
  assignmentId: string,
): string {
  return `ddp.annotation.conflict:${projectId}:${assignmentId}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFormValues(value: unknown): value is Record<string, EpisodeFormValues> {
  if (!isRecord(value)) {
    return false;
  }
  return Object.values(value).every((episode) => {
    if (!isRecord(episode)) {
      return false;
    }
    return Object.values(episode).every((field) => {
      if (field === null) {
        return true;
      }
      if (typeof field === "string" || typeof field === "boolean") {
        return true;
      }
      if (Array.isArray(field)) {
        return field.every((item) => typeof item === "string");
      }
      return (
        isRecord(field) &&
        typeof field.start === "string" &&
        typeof field.end === "string"
      );
    });
  });
}

function parseSnapshot(raw: string): ConflictSnapshot | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.version !== CONFLICT_SNAPSHOT_VERSION) {
    return null;
  }
  if (
    typeof parsed.projectId !== "string" ||
    typeof parsed.assignmentId !== "string" ||
    typeof parsed.taskCode !== "string" ||
    typeof parsed.expectedVersion !== "number" ||
    typeof parsed.currentVersion !== "number" ||
    typeof parsed.updatedAt !== "number" ||
    !Number.isFinite(parsed.updatedAt) ||
    !isFormValues(parsed.mine)
  ) {
    return null;
  }
  if (parsed.server !== null && !isFormValues(parsed.server)) {
    return null;
  }
  if (!isRecord(parsed.choices)) {
    return null;
  }
  const choices: Record<string, "mine" | "server"> = {};
  for (const [key, value] of Object.entries(parsed.choices)) {
    if (value === "mine" || value === "server") {
      choices[key] = value;
    }
  }
  return {
    version: CONFLICT_SNAPSHOT_VERSION,
    projectId: parsed.projectId,
    assignmentId: parsed.assignmentId,
    taskCode: parsed.taskCode,
    expectedVersion: parsed.expectedVersion,
    currentVersion: parsed.currentVersion,
    mine: parsed.mine,
    server: parsed.server ?? null,
    choices,
    updatedAt: parsed.updatedAt,
  };
}

/** 保存快照；存储不可用或写入抛错时返回 false（页面据此提示风险）。 */
export function saveConflictSnapshot(
  storage: ConflictStorage,
  snapshot: ConflictSnapshot,
): boolean {
  try {
    storage.setItem(
      conflictSnapshotKey(snapshot.projectId, snapshot.assignmentId),
      JSON.stringify(snapshot),
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * 读取某一分配的未合并冲突。
 *
 * 返回 `null` 表示没有快照；返回 `"invalid"` 表示有原始数据但无法解析——
 * 页面据此提示"冲突草稿损坏，请先复制/导出"而不是当作没有冲突。
 */
export type ConflictSnapshotLoad =
  | { status: "missing"; snapshot: null }
  | { status: "invalid"; snapshot: null }
  | { status: "ready"; snapshot: ConflictSnapshot };

export function loadConflictSnapshot(
  storage: ConflictStorage,
  identity: { projectId: string; assignmentId: string },
): ConflictSnapshotLoad {
  let raw: string | null;
  try {
    raw = storage.getItem(
      conflictSnapshotKey(identity.projectId, identity.assignmentId),
    );
  } catch {
    return { status: "invalid", snapshot: null };
  }
  if (raw === null) {
    return { status: "missing", snapshot: null };
  }
  const snapshot = parseSnapshot(raw);
  if (snapshot === null) {
    return { status: "invalid", snapshot: null };
  }
  return { status: "ready", snapshot };
}

/** 清除某分配的冲突快照（合并成功或用户显式丢弃时调用）。 */
export function clearConflictSnapshot(
  storage: ConflictStorage,
  projectId: string,
  assignmentId: string,
): void {
  try {
    storage.removeItem(conflictSnapshotKey(projectId, assignmentId));
  } catch {
    // 清理失败不影响提交结果；服务端状态才是权威。
  }
}

/** 浏览器 localStorage 适配；不可用（隐私模式/禁用）时返回 null。 */
export function browserConflictStorage(): ConflictStorage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const storage = window.localStorage;
    const probe = "__ddp_annotation_conflict_probe__";
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}
