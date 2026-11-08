import { useCallback, useEffect, useRef, useState } from "react";

import {
  browserDraftStorage,
  clearAnnotationDraft,
  createAnnotationDraft,
  hydrateFormValues,
  loadAnnotationDraft,
  saveAnnotationDraft,
  type AnnotationDraft,
  type DraftLoadStatus,
  type DraftStorage,
} from "../services/annotationDraft";
import type {
  DraftFieldValue,
  EpisodeFormValues,
} from "../services/annotationValidation";
import type { AnnotationSchema } from "../types/annotation";

export type DraftSaveState = "idle" | "saving" | "saved" | "failed" | "unavailable";

export interface UseAnnotationDraftResult {
  values: Record<string, EpisodeFormValues>;
  ready: boolean;
  notice: DraftLoadStatus | null;
  saveState: DraftSaveState;
  storageAvailable: boolean;
  updatedAt: number | null;
  updateField: (
    episodeId: string,
    labelKey: string,
    value: DraftFieldValue,
  ) => void;
  replaceValues: (values: Record<string, EpisodeFormValues>) => void;
  /** 立即落盘，供提交失败后刷新前保留当前输入。 */
  flushDraft: () => boolean;
  resetDraft: () => void;
}

const AUTOSAVE_DELAY_MS = 400;

/**
 * 本地草稿 Hook：按项目 + 分配 + Schema 快照隔离保存；刷新后恢复。
 * Schema 快照不一致时不加载旧草稿，避免把旧标签值写入新版本。
 */
export function useAnnotationDraft(params: {
  projectId: string;
  assignmentId: string;
  schema: AnnotationSchema | null;
  episodes: { episode_id: string }[];
}): UseAnnotationDraftResult {
  const { projectId, assignmentId, schema, episodes } = params;
  const storageRef = useRef<DraftStorage | null>(browserDraftStorage());
  const [values, setValues] = useState<Record<string, EpisodeFormValues>>({});
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<DraftLoadStatus | null>(null);
  const [saveState, setSaveState] = useState<DraftSaveState>("idle");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const storageAvailable = storageRef.current !== null;

  useEffect(() => {
    if (schema === null || episodes.length === 0) {
      setReady(false);
      return;
    }
    const storage = storageRef.current;
    if (storage === null) {
      setValues(hydrateFormValues(schema, episodes.map((e) => e.episode_id), null));
      setNotice(null);
      setSaveState("unavailable");
      setUpdatedAt(null);
      setReady(true);
      return;
    }
    const result = loadAnnotationDraft(storage, {
      projectId,
      assignmentId,
      schemaSha256: schema.schema_sha256,
    });
    const draft: AnnotationDraft | null =
      result.status === "ready" ? result.draft : null;
    setValues(
      hydrateFormValues(
        schema,
        episodes.map((episode) => episode.episode_id),
        draft,
      ),
    );
    setNotice(result.status);
    setUpdatedAt(draft?.updatedAt ?? null);
    setSaveState("idle");
    setReady(true);
  }, [assignmentId, episodes, projectId, schema]);

  useEffect(() => {
    if (!ready || schema === null) {
      return;
    }
    const storage = storageRef.current;
    if (storage === null) {
      return;
    }
    setSaveState("saving");
    const timer = window.setTimeout(() => {
      const now = Date.now();
      const draft = createAnnotationDraft(
        {
          projectId,
          assignmentId,
          schemaSha256: schema.schema_sha256,
        },
        values,
        now,
      );
      const saved = saveAnnotationDraft(storage, draft);
      setSaveState(saved ? "saved" : "failed");
      if (saved) {
        setUpdatedAt(now);
      }
    }, AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [assignmentId, projectId, ready, schema, values]);

  const updateField = useCallback(
    (episodeId: string, labelKey: string, value: DraftFieldValue) => {
      setValues((previous) => ({
        ...previous,
        [episodeId]: {
          ...(previous[episodeId] ?? {}),
          [labelKey]: value,
        },
      }));
    },
    [],
  );

  const replaceValues = useCallback(
    (next: Record<string, EpisodeFormValues>) => {
      setValues(next);
    },
    [],
  );

  const flushDraft = useCallback((): boolean => {
    const storage = storageRef.current;
    if (storage === null || schema === null) {
      return false;
    }
    const now = Date.now();
    const saved = saveAnnotationDraft(
      storage,
      createAnnotationDraft(
        { projectId, assignmentId, schemaSha256: schema.schema_sha256 },
        values,
        now,
      ),
    );
    setSaveState(saved ? "saved" : "failed");
    if (saved) {
      setUpdatedAt(now);
    }
    return saved;
  }, [assignmentId, projectId, schema, values]);

  const resetDraft = useCallback(() => {
    const storage = storageRef.current;
    if (storage !== null) {
      clearAnnotationDraft(storage, projectId, assignmentId);
    }
    if (schema !== null) {
      setValues(
        hydrateFormValues(
          schema,
          episodes.map((episode) => episode.episode_id),
          null,
        ),
      );
    }
    setNotice(null);
    setUpdatedAt(null);
    setSaveState(storage === null ? "unavailable" : "idle");
  }, [assignmentId, episodes, projectId, schema]);

  return {
    values,
    ready,
    notice,
    saveState,
    storageAvailable,
    updatedAt,
    updateField,
    replaceValues,
    flushDraft,
    resetDraft,
  };
}
