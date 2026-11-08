import type { AnnotationSchema } from "../types/annotation";
import {
  createEmptyFormValues,
  type DraftFieldValue,
  type EpisodeFormValues,
  type TimeRangeInput,
} from "./annotationValidation";

export const ANNOTATION_DRAFT_VERSION = 1;

export interface AnnotationDraft {
  version: typeof ANNOTATION_DRAFT_VERSION;
  projectId: string;
  assignmentId: string;
  schemaSha256: string;
  updatedAt: number;
  values: Record<string, EpisodeFormValues>;
}

export type DraftLoadStatus =
  | "ready"
  | "missing"
  | "invalid"
  | "incompatible";

export interface DraftLoadResult {
  status: DraftLoadStatus;
  draft: AnnotationDraft | null;
}

export interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function annotationDraftKey(
  projectId: string,
  assignmentId: string,
): string {
  return `ddp.annotation.draft:${projectId}:${assignmentId}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTimeRange(value: unknown): value is TimeRangeInput {
  return (
    isRecord(value) &&
    typeof value.start === "string" &&
    typeof value.end === "string"
  );
}

function isFieldValue(value: unknown): value is DraftFieldValue {
  if (value === null) {
    return true;
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return true;
  }
  if (Array.isArray(value)) {
    return value.every((item) => typeof item === "string");
  }
  return isTimeRange(value);
}

function isEpisodeValues(value: unknown): value is EpisodeFormValues {
  return (
    isRecord(value) &&
    Object.values(value).every((item) => isFieldValue(item))
  );
}

function parseDraft(raw: string): AnnotationDraft | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.version !== ANNOTATION_DRAFT_VERSION) {
    return null;
  }
  if (
    typeof parsed.projectId !== "string" ||
    typeof parsed.assignmentId !== "string" ||
    typeof parsed.schemaSha256 !== "string" ||
    typeof parsed.updatedAt !== "number" ||
    !Number.isFinite(parsed.updatedAt) ||
    !isRecord(parsed.values) ||
    !Object.values(parsed.values).every(isEpisodeValues)
  ) {
    return null;
  }
  return {
    version: ANNOTATION_DRAFT_VERSION,
    projectId: parsed.projectId,
    assignmentId: parsed.assignmentId,
    schemaSha256: parsed.schemaSha256,
    updatedAt: parsed.updatedAt,
    values: parsed.values as Record<string, EpisodeFormValues>,
  };
}

/** 保存草稿；存储不可用（隐私模式/配额）时返回 false，由页面显示 Failed。 */
export function saveAnnotationDraft(
  storage: DraftStorage,
  draft: AnnotationDraft,
): boolean {
  try {
    storage.setItem(
      annotationDraftKey(draft.projectId, draft.assignmentId),
      JSON.stringify(draft),
    );
    return true;
  } catch {
    return false;
  }
}

export function loadAnnotationDraft(
  storage: DraftStorage,
  identity: {
    projectId: string;
    assignmentId: string;
    schemaSha256: string;
  },
): DraftLoadResult {
  let raw: string | null;
  try {
    raw = storage.getItem(
      annotationDraftKey(identity.projectId, identity.assignmentId),
    );
  } catch {
    return { status: "invalid", draft: null };
  }
  if (raw === null) {
    return { status: "missing", draft: null };
  }
  const draft = parseDraft(raw);
  if (draft === null) {
    return { status: "invalid", draft: null };
  }
  if (
    draft.projectId !== identity.projectId ||
    draft.assignmentId !== identity.assignmentId ||
    draft.schemaSha256 !== identity.schemaSha256
  ) {
    return { status: "incompatible", draft };
  }
  return { status: "ready", draft };
}

export function clearAnnotationDraft(
  storage: DraftStorage,
  projectId: string,
  assignmentId: string,
): void {
  try {
    storage.removeItem(annotationDraftKey(projectId, assignmentId));
  } catch {
    // 清理失败不影响提交结果；后端提交才是权威状态。
  }
}

export function createAnnotationDraft(
  identity: {
    projectId: string;
    assignmentId: string;
    schemaSha256: string;
  },
  values: Record<string, EpisodeFormValues>,
  now = Date.now(),
): AnnotationDraft {
  return {
    version: ANNOTATION_DRAFT_VERSION,
    projectId: identity.projectId,
    assignmentId: identity.assignmentId,
    schemaSha256: identity.schemaSha256,
    updatedAt: now,
    values,
  };
}

/**
 * 用 Schema 默认值补齐草稿：Schema 新增必填标签时仍能展现字段并提示，
 * 同时保留草稿里已有的用户输入。
 */
export function hydrateFormValues(
  schema: AnnotationSchema,
  episodes: string[],
  draft: AnnotationDraft | null,
): Record<string, EpisodeFormValues> {
  const result: Record<string, EpisodeFormValues> = {};
  for (const episodeId of episodes) {
    const defaults = createEmptyFormValues(schema);
    const saved = draft?.values[episodeId] ?? {};
    for (const key of Object.keys(defaults)) {
      if (key in saved) {
        defaults[key] = saved[key] as DraftFieldValue;
      }
    }
    result[episodeId] = defaults;
  }
  return result;
}

export function browserDraftStorage(): DraftStorage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const storage = window.localStorage;
    const probe = "__ddp_annotation_probe__";
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}
