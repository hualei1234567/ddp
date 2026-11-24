import { useCallback, useState } from "react";

import { annotationApi } from "../services/annotation";
import {
  planConflictResubmission,
  type ConflictResubmitPlan,
} from "../services/annotationConflict";
import {
  conflictWithoutVersionsFeedback,
  useAnnotationConflictRecovery,
} from "./useAnnotationConflictRecovery";
import type { ConflictSnapshot } from "../services/annotationConflictDraft";
import type { EpisodeFormValues } from "../services/annotationValidation";
import {
  buildSubmissionRecords,
  type BuiltRecords,
} from "../services/annotationValidation";
import type { AnnotationFeedback } from "../services/annotationPresentation";
import type { AnnotationSchema, AnnotationSubmission, DataSource } from "../types/annotation";
import { loadLatestAssignmentSubmission } from "../services/annotationWorkbench";

/**
 * 工作台级别的冲突恢复编排（FR-019）。
 *
 * 本模块是 `useAnnotationConflictRecovery`（纯状态机）与页面之间的适配层：
 * - 把「提交失败」翻译成「进入恢复流或普通错误」；
 * - 合并结果回填后，按服务端真实状态决定 `retry-action` / `create-new-version`
 *   / `blocked`，并如实反馈——**任何路径都不谎报"已自动解决冲突"**。
 *
 * 之所以单独成 Hook 而不是写在页面里：页面已经承担数据加载与表单渲染，
 * 恢复流有 5 个分支（冲突识别、拉最新、合并、重提交、再冲突），
 * 全塞进页面会超过仓库的 500 行文件阈值，也让分支测试无从下手。
 */
export interface WorkbenchConflictDeps {
  projectId: string;
  assignmentId: string;
  taskId: string;
  taskCode: string;
  assignmentIdForCreate: string;
  dataSource: DataSource;
  schema: AnnotationSchema | null;
  episodes: { episode_id: string; episode_version: number }[];
  /** 当前表单值（每次调用读最新）。 */
  readValues: () => Record<string, EpisodeFormValues>;
  /** 合并结果回填。 */
  applyValues: (values: Record<string, EpisodeFormValues>) => void;
  /** 服务端当前最新提交（来自工作台缓存，可能已过期，仅作初值）。 */
  latestSubmission: AnnotationSubmission | null;
  setFeedback: (feedback: AnnotationFeedback | null) => void;
  /** 重新加载工作台（提交成功后）。 */
  reload: () => void;
  /** 清空本地草稿（提交成功后）。 */
  resetDraft: () => void;
  setBusyAction: (action: string | null) => void;
}

export interface WorkbenchConflictFlow {
  recovery: ReturnType<typeof useAnnotationConflictRecovery>;
  resubmitPlan: ConflictResubmitPlan | null;
  /**
   * 提交/动作失败入口。`sentVersion` 是本次请求用的 If-Match 版本，
   * 仅在服务端未回显 expected_version 时作为兜底——必须如实传入，
   * 传 0 会让"我基于哪一版"显示错误。
   * 返回 true 表示已按冲突处理，调用方不再展示通用错误。
   */
  enter: (caught: unknown, sentVersion: number) => Promise<boolean>;
  /** 用户确认合并后的重新提交。 */
  applyMerged: (merged: Record<string, EpisodeFormValues>) => Promise<void>;
  /** 放弃合并并刷新。 */
  discard: () => void;
  /**
   * 页面加载完成后尝试恢复未合并的冲突（刷新后的恢复路径）。
   *
   * 返回快照（含冲突当时的「我的版本」）表示恢复成功；页面应把它覆盖回表单，
   * 因为它才是用户真实的输入现场，优先级高于常规自动保存。
   */
  restore: () => ConflictSnapshot | null;
}

/** 构建合并记录的入口：与工作台提交共用同一份校验实现。 */
function buildRecords(
  deps: Pick<WorkbenchConflictDeps, "schema" | "episodes">,
  values: Record<string, EpisodeFormValues>,
): BuiltRecords {
  if (deps.schema === null) {
    return { records: [], issues: [] };
  }
  return buildSubmissionRecords(
    {
      ...deps.schema,
      labels: deps.schema.labels.filter(
        (label) => label.visibility === "ANNOTATOR",
      ),
    },
    deps.episodes.map((episode) => ({
      episodeId: episode.episode_id,
      episodeVersion: episode.episode_version,
    })),
    values,
  );
}

export function useWorkbenchConflictFlow(
  deps: WorkbenchConflictDeps,
): WorkbenchConflictFlow {
  const {
    projectId,
    taskId,
    taskCode,
    assignmentIdForCreate,
    dataSource,
    latestSubmission,
    setFeedback,
    reload,
    resetDraft,
    setBusyAction,
    readValues,
    applyValues,
    schema,
    episodes,
  } = deps;

  const recovery = useAnnotationConflictRecovery({
    projectId,
    assignmentId: deps.assignmentId,
    taskCode,
    readValues,
    applyValues,
  });
  const [resubmitPlan, setResubmitPlan] = useState<ConflictResubmitPlan | null>(
    null,
  );
  const restore = useCallback((): ConflictSnapshot | null => {
    const snapshot = recovery.restore();
    if (snapshot === null) {
      return null;
    }
    setResubmitPlan(planConflictResubmission(latestSubmission));
    setFeedback({
      kind: "warning",
      title: "检测到未合并的标注冲突",
      description: `上次提交与版本 ${snapshot.currentVersion} 冲突（你基于版本 ${snapshot.expectedVersion}），已恢复当时的输入。请先「复制草稿」保留记录，或逐项合并后再提交；本页不会自动覆盖服务端内容。`,
    });
    return snapshot;
  }, [latestSubmission, recovery, setFeedback]);

  const enter = useCallback(
    async (caught: unknown, sentVersion: number): Promise<boolean> => {
      const isConflict =
        typeof caught === "object" &&
        caught !== null &&
        "status" in caught &&
        (caught as { status?: number }).status === 409;
      if (!isConflict) {
        return false;
      }
      let serverSubmission: AnnotationSubmission | null = null;
      try {
        // 必须重新读：工作台缓存里的 latestSubmission 正是过期的那份，
        // 拿它当合并基准等于用旧值比旧值。
        serverSubmission = await loadLatestAssignmentSubmission(
          annotationApi,
          projectId,
          taskId,
          deps.assignmentId,
        );
      } catch {
        // 拉不到不影响"不丢数据"：退化为「冲突已发生但无法逐项比对」。
        serverSubmission = null;
      }
      const handled = recovery.handleSubmitError(
        caught,
        sentVersion,
        serverSubmission,
      );
      if (!handled) {
        setResubmitPlan(null);
        setFeedback(conflictWithoutVersionsFeedback());
        return true;
      }
      setResubmitPlan(
        planConflictResubmission(serverSubmission ?? latestSubmission),
      );
      return true;
    },
    [
      deps.assignmentId,
      latestSubmission,
      projectId,
      recovery,
      setFeedback,
      taskId,
    ],
  );

  const applyMerged = useCallback(
    async (merged: Record<string, EpisodeFormValues>): Promise<void> => {
      const plan = resubmitPlan ?? planConflictResubmission(latestSubmission);
      const built = buildRecords({ schema, episodes }, merged);
      if (plan.kind === "blocked") {
        // 状态机不允许时任何请求都会 409：只回填并如实说明，不发请求。
        recovery.commitMerge();
        setFeedback({
          kind: "warning",
          title: "合并结果已保留，但当前无法重新提交",
          description: plan.reason,
        });
        return;
      }
      if (built.issues.length > 0) {
        recovery.commitMerge();
        setFeedback({
          kind: "error",
          title: "合并结果未通过完整性校验",
          description: `还有 ${built.issues.length} 项需要修正；已回填表单，请按字段提示补齐后再提交。`,
        });
        return;
      }
      setBusyAction("resubmit");
      recovery.setResubmitting(true);
      // 记录本次实际使用的 If-Match，供再次冲突时兜底显示"我基于哪一版"。
      let lastSentVersion = plan.kind === "retry-action" ? plan.version : 0;
      try {
        let submissionId: string;
        let version: number;
        if (plan.kind === "retry-action") {
          submissionId = plan.submissionId;
          version = plan.version;
        } else {
          const created = await annotationApi.createSubmission(projectId, {
            assignment_id: assignmentIdForCreate,
            records: built.records,
            data_source: dataSource,
          });
          submissionId = created.submission_id;
          version = created.version;
        }
        lastSentVersion = version;
        await annotationApi.actOnSubmission(projectId, submissionId, "submit", {
          version,
        });
        recovery.setResubmitting(false);
        recovery.commitMerge();
        setResubmitPlan(null);
        resetDraft();
        setFeedback({
          kind: "success",
          title: "冲突已按你的逐项选择合并并重新提交",
          description:
            "服务端已接受新版本，等待复核。合并是你逐项选择的结果，不是系统自动合并。",
        });
        reload();
      } catch (caught) {
        recovery.setResubmitting(false);
        // 再次冲突（他人又更新了）：重新进入恢复流，输入仍然不丢。
        const handled = await enter(caught, lastSentVersion);
        if (!handled) {
          setFeedback({
            kind: "error",
            title: "重新提交失败",
            description: `合并结果已保留在本地草稿中。${String(caught)}`,
          });
        }
      } finally {
        setBusyAction(null);
      }
    },
    [
      assignmentIdForCreate,
      dataSource,
      enter,
      episodes,
      latestSubmission,
      projectId,
      recovery,
      reload,
      resetDraft,
      resubmitPlan,
      schema,
      setBusyAction,
      setFeedback,
    ],
  );

  return {
    recovery,
    resubmitPlan,
    enter,
    applyMerged,
    discard: recovery.discardAndRefresh,
    restore,
  };
}
