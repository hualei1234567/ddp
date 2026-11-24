/**
 * 分配前实时容量预览（FR-020）。
 *
 * 行为约定：
 *
 * - **选中即查**：`assigneeId` 一变就请求，不等提交；
 * - **防抖 + 取消**：计划项数/工时通常是连续敲键盘改的，每敲一下发一次请求
 *   既浪费又会让提示闪烁；上一次未完成的请求必须被 abort，否则晚到的旧结果
 *   会覆盖新结果（经典竞态）；
 * - **失败不阻断**：预览失败只降级成"提示不可用"，提交仍交给服务端既有容量
 *   策略裁决。把预览失败当成禁止提交是越权——它只是一个提示。
 * - **不缓存跨人结果**：`data` 必须属于当前 `assigneeId`，否则切人瞬间会显示
 *   上一个人的负载，比不显示更糟。
 */
import { useEffect, useState } from "react";

import { annotationApi } from "../services/annotation";
import { ApiError } from "../services/api";
import type { AssignmentCapacityPreview } from "../types/annotationCapacityPreview";

/** 计划工时/项数连续编辑时的合并窗口。 */
export const CAPACITY_PREVIEW_DEBOUNCE_MS = 250;

export interface AssignmentCapacityPreviewState {
  /** 仅当它是当前 assigneeId 的结果时才有值。 */
  data: AssignmentCapacityPreview | null;
  loading: boolean;
  /** 预览请求失败；调用方应降级提示，而不是据此禁止提交。 */
  error: ApiError | null;
}

export interface UseAssignmentCapacityPreviewOptions {
  projectId: string;
  /** 选中的被分配人；为空表示尚未选择，不发请求。 */
  assigneeId: string | null | undefined;
  plannedItemCount: number;
  plannedMinutes: number;
  /** 抽屉关闭时传 false，避免在后台继续发请求。 */
  enabled?: boolean;
  debounceMs?: number;
}

const IDLE: AssignmentCapacityPreviewState = {
  data: null,
  loading: false,
  error: null,
};

export function useAssignmentCapacityPreview({
  projectId,
  assigneeId,
  plannedItemCount,
  plannedMinutes,
  enabled = true,
  debounceMs = CAPACITY_PREVIEW_DEBOUNCE_MS,
}: UseAssignmentCapacityPreviewOptions): AssignmentCapacityPreviewState {
  const [state, setState] = useState<AssignmentCapacityPreviewState>(IDLE);

  useEffect(() => {
    if (!enabled || !assigneeId) {
      // 清空而不是保留上一个人的结果：错位的负载提示比没有提示更危险。
      setState(IDLE);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setState((previous) => ({ ...previous, loading: true, error: null }));

    const timer = setTimeout(() => {
      annotationApi
        .previewAssignmentCapacity(
          projectId,
          {
            assignee_ids: [assigneeId],
            planned_item_count: plannedItemCount,
            planned_minutes: plannedMinutes,
          },
          { signal: controller.signal },
        )
        .then((previews) => {
          if (!active) {
            return;
          }
          const matched = previews.find(
            (item) => item.assignee_id === assigneeId,
          );
          setState({
            // 请求成功但服务端没有返回这个人：如实当作无数据，不臆造。
            data: matched ?? null,
            loading: false,
            error: null,
          });
        })
        .catch((caught: unknown) => {
          if (!active) {
            return;
          }
          setState({
            data: null,
            loading: false,
            error:
              caught instanceof ApiError
                ? caught
                : new ApiError({
                    kind: "network",
                    message: "负载预览失败，请稍后重试。",
                    cause: caught,
                  }),
          });
        });
    }, debounceMs);

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [
    assigneeId,
    debounceMs,
    enabled,
    plannedItemCount,
    plannedMinutes,
    projectId,
  ]);

  return state;
}
