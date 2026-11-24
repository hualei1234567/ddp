import { useCallback, useState } from "react";

import {
  buildConflictContext,
  buildConflictDraftExport,
  conflictDraftFileName,
  copyTextToClipboard,
  downloadTextFile,
  mergeByChoice,
  parseConflictVersions,
  serializeConflictDraft,
  type ConflictContext,
  type MergeChoice,
} from "../services/annotationConflict";
import {
  browserConflictStorage,
  clearConflictSnapshot,
  loadConflictSnapshot,
  saveConflictSnapshot,
  type ConflictSnapshot,
} from "../services/annotationConflictDraft";
import type { EpisodeFormValues } from "../services/annotationValidation";
import type { AnnotationSubmission } from "../types/annotation";

export type ConflictPhase = "idle" | "detected" | "resubmitting";

export interface ConflictRecoveryState {
  phase: ConflictPhase;
  /** 冲突上下文；`phase === "detected"` 时必有值。 */
  context: ConflictContext | null;
  /** 恢复的合并选择（刷新后继续沿用）。 */
  choices: Record<string, MergeChoice>;
  /** 快照落盘失败时提示"无法跨刷新恢复"。 */
  persistenceWarning: string | null;
}

const INITIAL_STATE: ConflictRecoveryState = {
  phase: "idle",
  context: null,
  choices: {},
  persistenceWarning: null,
};

export interface UseAnnotationConflictRecoveryParams {
  projectId: string;
  assignmentId: string;
  taskCode: string;
  /** 取当前表单值：冲突发生时用它保存"我的版本"。 */
  readValues: () => Record<string, EpisodeFormValues>;
  /** 合并后回填表单（用户可见、可继续编辑）。 */
  applyValues: (values: Record<string, EpisodeFormValues>) => void;
}

export interface UseAnnotationConflictRecoveryResult
  extends ConflictRecoveryState {
  /**
   * 提交失败时调用。返回 true 表示已识别为版本冲突并进入恢复流，
   * 调用方**不应**再展示通用错误反馈。
   */
  handleSubmitError: (
    caught: unknown,
    sentVersion: number,
    serverSubmission: AnnotationSubmission | null,
  ) => boolean;
  /** 复制/下载草稿；`method="clipboard"` 失败时自动降级为下载并返回 false。 */
  copyDraft: (method: "clipboard" | "download") => Promise<boolean>;
  /** 用户确认合并结果：回填表单并清除快照。 */
  commitMerge: () => Record<string, EpisodeFormValues> | null;
  /** 放弃合并并刷新：清除快照（数据仍可先复制）。 */
  discardAndRefresh: () => void;
  /** 关闭对话框但保留快照，下次进入/刷新可恢复。 */
  dismiss: () => void;
  /** 页面加载时尝试恢复未合并的冲突（刷新后路径）。 */
  restore: () => ConflictSnapshot | null;
  /** 标记"正在用最新版本重提交"。 */
  setResubmitting: (busy: boolean) => void;
}

/**
 * FR-019 标注冲突恢复编排 Hook。
 *
 * ## 为什么不把这些逻辑直接写在页面里
 *
 * 恢复流有 4 个必须同时成立的不变量，散在页面里很容易漏一条：
 * 1. 识别 409 后**不能**走通用错误提示（否则用户以为只是失败）；
 * 2. 冲突发生的那一刻就要把「我的版本」落盘（后续编辑会覆盖表单值）；
 * 3. 重启页面后要能读回快照，而不是"没有冲突"；
 * 4. 合并/放弃必须显式清除快照，否则已解决的冲突会反复弹出。
 *
 * 集中在一个 Hook 里，页面只负责渲染，测试也只测这一个状态机。
 */
export function useAnnotationConflictRecovery(
  params: UseAnnotationConflictRecoveryParams,
): UseAnnotationConflictRecoveryResult {
  const { projectId, assignmentId, taskCode, readValues, applyValues } = params;
  const [state, setState] = useState<ConflictRecoveryState>(INITIAL_STATE);

  const storage = browserConflictStorage();

  const handleSubmitError = useCallback(
    (
      caught: unknown,
      sentVersion: number,
      serverSubmission: AnnotationSubmission | null,
    ): boolean => {
      const versions = parseConflictVersions(caught, sentVersion);
      if (versions === null) {
        return false;
      }
      // 冲突瞬间的"我的版本"必须立刻落盘：之后用户任何一次编辑都会把
      // 表单值推进到新状态，那时再存就不是"冲突时的输入"了。
      const mineSnapshot = readValues();
      const context = buildConflictContext(versions, serverSubmission);
      let persistenceWarning: string | null = null;
      if (storage === null) {
        persistenceWarning =
          "本地存储不可用：本次冲突无法跨刷新恢复，请先「复制草稿」保留输入。";
      } else {
        const saved = saveConflictSnapshot(storage, {
          version: 1,
          projectId,
          assignmentId,
          taskCode,
          expectedVersion: versions.expectedVersion,
          currentVersion: versions.currentVersion,
          mine: mineSnapshot,
          server: context.serverValues,
          choices: {},
          updatedAt: Date.now(),
        });
        if (!saved) {
          persistenceWarning =
            "冲突草稿写入本地失败（可能空间不足），请先「复制草稿」保留输入。";
        }
      }
      setState({
        phase: "detected",
        context,
        choices: {},
        persistenceWarning,
      });
      return true;
    },
    [assignmentId, projectId, readValues, storage, taskCode],
  );

  const copyDraft = useCallback(
    async (method: "clipboard" | "download"): Promise<boolean> => {
      if (state.context === null) {
        return false;
      }
      const draft = buildConflictDraftExport({
        projectId,
        assignmentId,
        taskCode,
        context: state.context,
        mine: readValues(),
      });
      const text = serializeConflictDraft(draft);
      if (method === "download") {
        downloadTextFile(conflictDraftFileName(draft), text);
        return true;
      }
      const copied = await copyTextToClipboard(text);
      if (!copied) {
        // 剪贴板不可用（内网 http、权限被拒）不是失败：降级为下载文件。
        downloadTextFile(conflictDraftFileName(draft), text);
      }
      return copied;
    },
    [assignmentId, projectId, readValues, state.context, taskCode],
  );

  const commitMerge = useCallback((): Record<string, EpisodeFormValues> | null => {
    const context = state.context;
    if (context === null || context.serverValues === null) {
      return null;
    }
    const merged = mergeByChoice(readValues(), context.serverValues, state.choices);
    applyValues(merged);
    if (storage !== null) {
      clearConflictSnapshot(storage, projectId, assignmentId);
    }
    setState(INITIAL_STATE);
    return merged;
  }, [applyValues, assignmentId, projectId, readValues, state.choices, state.context, storage]);

  const discardAndRefresh = useCallback(() => {
    if (storage !== null) {
      clearConflictSnapshot(storage, projectId, assignmentId);
    }
    setState(INITIAL_STATE);
  }, [assignmentId, projectId, storage]);

  const dismiss = useCallback(() => {
    // 只关弹窗，保留快照：用户可能只是想先看一眼别处再回来合并。
    setState((previous) => ({ ...previous, phase: "idle" }));
  }, []);

  const restore = useCallback((): ConflictSnapshot | null => {
    if (storage === null) {
      return null;
    }
    const loaded = loadConflictSnapshot(storage, { projectId, assignmentId });
    if (loaded.status !== "ready") {
      return null;
    }
    const snapshot = loaded.snapshot;
    setState({
      phase: "detected",
      context: {
        expectedVersion: snapshot.expectedVersion,
        currentVersion: snapshot.currentVersion,
        serverValues: snapshot.server,
      },
      choices: snapshot.choices,
      persistenceWarning: null,
    });
    return snapshot;
  }, [assignmentId, projectId, storage]);

  const setResubmitting = useCallback((busy: boolean) => {
    setState((previous) => ({
      ...previous,
      phase: busy ? "resubmitting" : previous.phase,
    }));
  }, []);

  return {
    ...state,
    handleSubmitError,
    copyDraft,
    commitMerge,
    discardAndRefresh,
    dismiss,
    restore,
    setResubmitting,
  };
}

/** 409 但服务端没给出 current_version 时的兜底提示（不猜版本号）。 */
export function conflictWithoutVersionsFeedback(): {
  kind: "warning";
  title: string;
  description: string;
} {
  return {
    kind: "warning",
    title: "版本冲突：他人已更新此标注",
    description:
      "服务端未返回版本号，无法自动判断差异。你的本地输入已保留在本机草稿中；请刷新页面基于最新版本重新填写，或先复制草稿保留输入。页面不会自动覆盖他人修改。",
  };
}
