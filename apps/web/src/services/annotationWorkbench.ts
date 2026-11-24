import type {
  AnnotationAssignment,
  AnnotationBatch,
  AnnotationSampleSet,
  AnnotationSchema,
  AnnotationSubmission,
  AnnotationTask,
} from "../types/annotation";
import type { Episode } from "../types/collection";
import { ApiError } from "./api";
import type { AnnotationApi } from "./annotation";
import type { AiSuggestion } from "./annotationAiParsers";
import {
  buildSubmissionRecords,
  type BuiltRecords,
  type EpisodeFormValues,
} from "./annotationValidation";
import { fetchAllCursorPages } from "./cursorPage";
import type { CollectionsApi } from "./collections";

export interface WorkbenchPartialError {
  scope: string;
  error: ApiError;
}

export interface WorkbenchData {
  assignment: AnnotationAssignment;
  task: AnnotationTask;
  batch: AnnotationBatch;
  schema: AnnotationSchema;
  sampleSet: AnnotationSampleSet | null;
  episodes: Episode[];
  /** 服务端已有的本人 DRAFT 提交；存在时不允许再创建新草稿。 */
  existingDraft: AnnotationSubmission | null;
  /** 当前分配的最新提交版本，用于展示“已提交，等待复核”等状态。 */
  latestSubmission: AnnotationSubmission | null;
  /** AI 预标注建议（按最新提交挂靠；无提交时为空。加载失败不阻塞工作台）。 */
  aiSuggestions: AiSuggestion[];
}

export interface WorkbenchLoadResult {
  data: WorkbenchData | null;
  error: ApiError | null;
  partialErrors: WorkbenchPartialError[];
}

export function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({ kind: "network", message: fallback, cause: caught });
}

function latestByVersion(
  submissions: AnnotationSubmission[],
): AnnotationSubmission | null {
  if (submissions.length === 0) {
    return null;
  }
  return [...submissions].sort(
    (left, right) => right.submission_version - left.submission_version,
  )[0] ?? null;
}

async function loadEpisodes(
  collections: CollectionsApi,
  projectId: string,
  episodeIds: string[],
  signal: AbortSignal,
  partialErrors: WorkbenchPartialError[],
): Promise<Episode[]> {
  const results = await Promise.allSettled(
    episodeIds.map((episodeId) =>
      collections.getEpisode(projectId, episodeId, { signal }),
    ),
  );
  const episodes: Episode[] = [];
  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      episodes.push(result.value);
    } else if (!signal.aborted) {
      partialErrors.push({
        scope: `Episode ${episodeIds[index] ?? ""}`,
        error: asApiError(result.reason, "Episode 加载失败"),
      });
    }
  });
  return episodes;
}

/**
 * 工作台聚合加载：分配 → 任务/批次 → Schema → 样本集/Episode/提交。
 *
 * 分配、任务、批次与 Schema 是渲染表单的必需数据；样本集、Episode 详情与
 * 历史提交失败时保留可用数据并返回 Partial 错误，不伪造业务字段。
 */
export async function loadAnnotationWorkbench(
  api: AnnotationApi,
  collections: CollectionsApi,
  projectId: string,
  assignmentId: string,
  signal: AbortSignal,
): Promise<WorkbenchLoadResult> {
  const partialErrors: WorkbenchPartialError[] = [];
  try {
    const assignment = await api.getAssignment(projectId, assignmentId, {
      signal,
    });
    const [task, batch] = await Promise.all([
      api.getTask(projectId, assignment.task_id, { signal }),
      api.getBatch(projectId, assignment.batch_id, { signal }),
    ]);
    const schema = await api.getSchema(projectId, task.schema_id, { signal });

    let sampleSet: AnnotationSampleSet | null = null;
    try {
      sampleSet = await api.getSampleSet(projectId, task.sample_set_id, {
        signal,
      });
    } catch (caught) {
      if (!signal.aborted) {
        partialErrors.push({
          scope: "标注样本集",
          error: asApiError(caught, "标注样本集加载失败"),
        });
      }
    }

    const episodeIds =
      batch.item_refs.length > 0
        ? batch.item_refs
        : (sampleSet?.episode_refs.map((item) => item.episode_id) ?? []);
    const episodes = await loadEpisodes(
      collections,
      projectId,
      episodeIds,
      signal,
      partialErrors,
    );

    let submissions: AnnotationSubmission[] = [];
    try {
      // 必须翻完全部页：只读第一页时，提交数超过 20 的任务可能看不到
      // 本人既有 DRAFT，从而新建第二份重复草稿（服务端会拒绝，但用户会
      // 得到一条莫名其妙的错误）。
      const allSubmissions = await fetchAllCursorPages(
        (options) =>
          api.listSubmissions(projectId, task.task_id, { ...options, signal }),
        (item) => item.submission_id,
      );
      submissions = allSubmissions.filter(
        (item) => item.assignment_id === assignment.assignment_id,
      );
    } catch (caught) {
      if (!signal.aborted) {
        partialErrors.push({
          scope: "标注提交历史",
          error: asApiError(caught, "标注提交历史加载失败"),
        });
      }
    }

    const latestSubmission = latestByVersion(submissions);
    let existingDraft =
      submissions.find((item) => item.status === "DRAFT") ?? null;
    if (existingDraft !== null) {
      try {
        existingDraft = await api.getSubmission(
          projectId,
          existingDraft.submission_id,
          { signal },
        );
      } catch (caught) {
        if (!signal.aborted) {
          partialErrors.push({
            scope: "服务端草稿",
            error: asApiError(caught, "服务端草稿加载失败"),
          });
        }
      }
    }

    // V31-046 切片 D：AI 建议按最新提交挂靠。无提交=无建议挂靠点，空数组；
    // 加载失败只记 partialError，不阻塞工作台主链。
    let aiSuggestions: AiSuggestion[] = [];
    if (latestSubmission !== null) {
      try {
        const page = await api.listAiSuggestions(
          projectId,
          latestSubmission.submission_id,
          { signal },
        );
        aiSuggestions = page.items;
      } catch (caught) {
        if (!signal.aborted) {
          partialErrors.push({
            scope: "AI 建议",
            error: asApiError(caught, "AI 建议加载失败"),
          });
        }
      }
    }

    return {
      data: {
        assignment,
        task,
        batch,
        schema,
        sampleSet,
        episodes,
        existingDraft,
        latestSubmission,
        aiSuggestions,
      },
      error: null,
      partialErrors,
    };
  } catch (caught) {
    if (signal.aborted) {
      return { data: null, error: null, partialErrors };
    }
    return {
      data: null,
      error: asApiError(caught, "标注工作台加载失败"),
      partialErrors,
    };
  }
}

/** Schema 快照与任务冻结值一致性检查；不一致时禁止提交。 */
export function schemaSnapshotMatches(
  task: AnnotationTask,
  schema: AnnotationSchema,
): boolean {
  return (
    task.schema_id === schema.schema_id &&
    task.schema_version === schema.schema_version &&
    task.schema_sha256 === schema.schema_sha256 &&
    schema.status === "PUBLISHED"
  );
}

/** 标注员可见标签；GOLD_ONLY/SYSTEM_ONLY/REVIEWER_ONLY 不在工作台渲染。 */
export function visibleLabelDefinitions(schema: AnnotationSchema) {
  return schema.labels.filter((label) => label.visibility === "ANNOTATOR");
}

/** 存在当前角色不可见但必填的标签时，前端无法完整填写，应禁用提交。 */
export function hasHiddenRequiredLabels(schema: AnnotationSchema): boolean {
  return schema.labels.some(
    (label) => label.required && label.visibility !== "ANNOTATOR",
  );
}

/**
 * 用工作台当前表单生成提交记录；只校验标注员可见标签。
 * 服务端仍按完整 Schema 快照做权威校验。
 */
export function buildWorkbenchRecords(
  data: WorkbenchData,
  values: Record<string, EpisodeFormValues>,
): BuiltRecords {
  const visibleSchema: AnnotationSchema = {
    ...data.schema,
    labels: visibleLabelDefinitions(data.schema),
  };
  const episodes = data.episodes.map((episode) => ({
    episodeId: episode.episode_id,
    episodeVersion: episode.episode_version,
  }));
  return buildSubmissionRecords(visibleSchema, episodes, values);
}

/**
 * 冲突发生时读取服务端**权威最新提交**（含记录内容）。
 *
 * 不复用工作台已加载的 `data.latestSubmission`：那份数据是上次加载的快照，
 * 正是它过期才产生了本次 409。恢复流展示给用户的"服务端最新版本"必须是
 * 现在的真实状态，否则合并基准还是错的。
 *
 * 走「列表找最大版本 → 取详情」两步而不是只读列表：列表按可见性返回，
 * 但不内联记录内容，差异计算需要 `records`。
 */
export async function loadLatestAssignmentSubmission(
  api: AnnotationApi,
  projectId: string,
  taskId: string,
  assignmentId: string,
  signal?: AbortSignal,
): Promise<AnnotationSubmission | null> {
  const all = await fetchAllCursorPages(
    (options) => api.listSubmissions(projectId, taskId, { ...options, signal }),
    (item) => item.submission_id,
  );
  const mine = all.filter((item) => item.assignment_id === assignmentId);
  const latest = latestByVersion(mine);
  if (latest === null) {
    return null;
  }
  if (latest.records.length > 0) {
    return latest;
  }
  return api.getSubmission(projectId, latest.submission_id, { signal });
}
