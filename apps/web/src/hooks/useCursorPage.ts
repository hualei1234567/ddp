import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../services/api";
import { mergeCursorPageItems } from "../services/cursorPage";
import type { CursorPage } from "../types/device";

export interface CursorPageRequestOptions {
  cursor?: string;
  signal: AbortSignal;
}

export type CursorPageFetcher<T> = (
  options: CursorPageRequestOptions,
) => Promise<CursorPage<T>>;

export interface CursorPageState<T> {
  /** 已加载的全部条目，按页顺序累积并按主键去重。 */
  items: T[];
  nextCursor: string | null;
  /** 首次加载（尚无数据）时为 true。 */
  loading: boolean;
  /** 手动刷新（重新从第一页加载）时为 true。 */
  refreshing: boolean;
  /** 加载更多时为 true。 */
  loadingMore: boolean;
  /** 首次加载或刷新失败；加载更多失败单独放在 loadMoreError。 */
  error: ApiError | null;
  loadMoreError: ApiError | null;
  /** 刷新失败但保留旧数据。 */
  stale: boolean;
  updatedAt: number | null;
}

export interface UseCursorPageResult<T> extends CursorPageState<T> {
  hasMore: boolean;
  /** 重新从第一页加载，成功后替换已加载数据。 */
  reload: () => void;
  /** 使用 next_cursor 追加下一页。 */
  loadMore: () => void;
}

const INITIAL_STATE: CursorPageState<never> = {
  items: [],
  nextCursor: null,
  loading: true,
  refreshing: false,
  loadingMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
  updatedAt: null,
};

type LoadMode = "initial" | "refresh" | "more";

function toApiError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError({
        kind: "network",
        message: "请求失败，请检查网络或服务状态。",
        cause: caught,
      });
}

/**
 * 游标分页请求状态机：首屏加载、加载更多、刷新、取消与 Stale。
 *
 * 与 `useRequest` 不同的是，本 Hook 负责跨页累积数据；筛选与统计由调用方
 * 基于 `items`（即已加载数据）计算，页面必须明确标注作用域。
 *
 * 调用方在 projectId / deviceId 变化时应通过组件 `key` 重新挂载，
 * 本 Hook 只对挂载期内的同一数据源分页。
 */
export function useCursorPage<T>(
  request: CursorPageFetcher<T>,
  getKey: (item: T) => string,
): UseCursorPageResult<T> {
  const [state, setState] = useState<CursorPageState<T>>(INITIAL_STATE);
  const requestRef = useRef(request);
  const getKeyRef = useRef(getKey);
  const stateRef = useRef(state);
  const controllerRef = useRef<AbortController | null>(null);

  requestRef.current = request;
  getKeyRef.current = getKey;
  stateRef.current = state;

  const execute = useCallback(async (mode: LoadMode) => {
    const current = stateRef.current;
    const cursor = mode === "more" ? current.nextCursor : null;
    if (mode === "more") {
      if (!cursor || current.loading || current.refreshing || current.loadingMore) {
        return;
      }
    }
    if (mode === "refresh" && current.refreshing) {
      return;
    }

    // 中止上一请求而不是拦截新请求：StrictMode 下 effect 会挂载两次，
    // 第二次首屏加载必须能真正发出；手动刷新也能中断进行中的加载更多。
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setState((previous) => {
      const next: CursorPageState<T> = {
        ...previous,
        loading: mode === "initial" && previous.items.length === 0,
        refreshing: mode === "refresh",
        loadingMore: mode === "more",
        loadMoreError: null,
      };
      stateRef.current = next;
      return next;
    });

    try {
      const page = await requestRef.current({
        cursor: cursor ?? undefined,
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        return;
      }

      setState((previous) => {
        const items =
          mode === "more"
            ? mergeCursorPageItems(
                previous.items,
                page.items,
                getKeyRef.current,
              )
            : page.items;
        const next: CursorPageState<T> = {
          items,
          nextCursor: page.next_cursor,
          loading: false,
          refreshing: false,
          loadingMore: false,
          error: null,
          loadMoreError: null,
          stale: false,
          updatedAt: Date.now(),
        };
        stateRef.current = next;
        return next;
      });
    } catch (caught) {
      if (controller.signal.aborted) {
        return;
      }
      const error = toApiError(caught);
      setState((previous) => {
        const next: CursorPageState<T> =
          mode === "more"
            ? {
                ...previous,
                loadingMore: false,
                loadMoreError: error,
              }
            : {
                ...previous,
                loading: false,
                refreshing: false,
                loadingMore: false,
                error,
                stale: previous.items.length > 0,
              };
        stateRef.current = next;
        return next;
      });
    }
  }, []);

  useEffect(() => {
    void execute("initial");
    return () => {
      controllerRef.current?.abort();
    };
  }, [execute]);

  const reload = useCallback(() => {
    void execute("refresh");
  }, [execute]);

  const loadMore = useCallback(() => {
    void execute("more");
  }, [execute]);

  return {
    ...state,
    hasMore: state.nextCursor !== null,
    reload,
    loadMore,
  };
}
