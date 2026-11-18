import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../services/api";
import {
  mergeCursorPageItems,
  mergeCursorPageRefresh,
} from "../services/cursorPage";
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
  /** 原地合并刷新（自动轮询）时为 true；不阻塞已展示的数据。 */
  merging: boolean;
  /** 首次加载或刷新失败；加载更多失败单独放在 loadMoreError。 */
  error: ApiError | null;
  loadMoreError: ApiError | null;
  /** 原地合并刷新失败；已加载数据保留时同时标记 stale。 */
  mergeError: ApiError | null;
  /** 刷新失败但保留旧数据。 */
  stale: boolean;
  updatedAt: number | null;
}

export interface UseCursorPageResult<T> extends CursorPageState<T> {
  hasMore: boolean;
  /** 重新从第一页加载，成功后替换已加载数据（会丢掉已加载的后续页）。 */
  reload: () => void;
  /** 使用 next_cursor 追加下一页。 */
  loadMore: () => void;
  /**
   * 原地合并刷新：只拉第一页，保留已加载页与顺序，按主键更新/新增/删除，
   * 不跳回第一页、不清空 next_cursor。自动轮询应使用本方法；
   * 需要权威重置（例如执行了写操作）时仍用 `reload`。
   */
  refreshInPlace: () => void;
}

const INITIAL_STATE: CursorPageState<never> = {
  items: [],
  nextCursor: null,
  loading: true,
  refreshing: false,
  loadingMore: false,
  merging: false,
  error: null,
  loadMoreError: null,
  mergeError: null,
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
 * 请求代数：保证「后发起者胜」，丢弃过期响应。
 *
 * 自动轮询的合并刷新、用户的加载更多与手动刷新可能交错；只有最新一次
 * 同类请求的响应可以写状态。即使 `AbortController` 没能立刻终止在途请求
 * （例如请求已经解析、只是回调排在后面），旧响应的代数校验也会失败，
 * 因此不会把列表回退成旧快照。
 */
export interface CursorPageRequestTracker {
  /** 开始一次全量加载（首屏 / 手动刷新 / 加载更多）；作废在途的合并刷新。 */
  beginFullLoad: () => number;
  isCurrentFullLoad: (token: number) => boolean;
  /** 开始一次原地合并刷新。 */
  beginMerge: () => number;
  isCurrentMerge: (token: number) => boolean;
}

export function createCursorPageRequestTracker(): CursorPageRequestTracker {
  let fullLoadToken = 0;
  let mergeToken = 0;
  return {
    beginFullLoad() {
      fullLoadToken += 1;
      // 全量加载会重建列表，先前的合并结果不再适用。
      mergeToken += 1;
      return fullLoadToken;
    },
    isCurrentFullLoad(token) {
      return token === fullLoadToken;
    },
    beginMerge() {
      mergeToken += 1;
      return mergeToken;
    },
    isCurrentMerge(token) {
      return token === mergeToken;
    },
  };
}

/**
 * 合并刷新成功后的状态迁移。
 *
 * 空页不清空已加载数据：轮询期间的空响应可能来自瞬时异常，直接清空会让
 * 页面闪成空态。需要权威清空时由 `reload` 全量刷新。
 */
export function mergedRefreshSuccessState<T>(
  previous: CursorPageState<T>,
  page: CursorPage<T>,
  getKey: (item: T) => string,
): CursorPageState<T> {
  const base: CursorPageState<T> = {
    ...previous,
    merging: false,
    mergeError: null,
    stale: false,
    updatedAt: Date.now(),
  };
  if (page.items.length === 0) {
    return base;
  }
  return {
    ...base,
    items: mergeCursorPageRefresh(previous.items, page.items, getKey).items,
    nextCursor: page.next_cursor,
  };
}

/**
 * 合并刷新失败后的状态迁移：保留已加载数据并标记 Stale；
 * 尚无数据时才进入首屏错误态。
 */
export function mergedRefreshFailureState<T>(
  previous: CursorPageState<T>,
  error: ApiError,
): CursorPageState<T> {
  const hasData = previous.items.length > 0;
  return {
    ...previous,
    merging: false,
    mergeError: error,
    stale: hasData,
    error: hasData ? previous.error : error,
  };
}

/**
 * 游标分页请求状态机：首屏加载、加载更多、刷新、原地合并刷新、取消与 Stale。
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
  const mergeControllerRef = useRef<AbortController | null>(null);
  const trackerRef = useRef<CursorPageRequestTracker>(
    createCursorPageRequestTracker(),
  );

  requestRef.current = request;
  getKeyRef.current = getKey;
  stateRef.current = state;

  // 同步写入 stateRef 再触发渲染：并发守卫（加载更多/刷新中跳过轮询）
  // 依赖的是「已经发生」的状态，而不是尚未渲染的 React state。
  const commit = useCallback(
    (updater: (previous: CursorPageState<T>) => CursorPageState<T>) => {
      const next = updater(stateRef.current);
      stateRef.current = next;
      setState(next);
    },
    [],
  );

  const execute = useCallback(
    (mode: LoadMode) => {
      const current = stateRef.current;
      const cursor = mode === "more" ? current.nextCursor : null;
      if (mode === "more") {
        if (
          !cursor ||
          current.loading ||
          current.refreshing ||
          current.loadingMore
        ) {
          return;
        }
      }
      if (mode === "refresh" && current.refreshing) {
        return;
      }

      const token = trackerRef.current.beginFullLoad();
      // 中止上一请求而不是拦截新请求：StrictMode 下 effect 会挂载两次，
      // 第二次首屏加载必须能真正发出；手动刷新也能中断进行中的加载更多
      // 与原地合并刷新。
      controllerRef.current?.abort();
      mergeControllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      commit((previous) => ({
        ...previous,
        loading: mode === "initial" && previous.items.length === 0,
        refreshing: mode === "refresh",
        loadingMore: mode === "more",
        merging: false,
        mergeError: null,
        loadMoreError: null,
      }));

      void (async () => {
        try {
          const page = await requestRef.current({
            cursor: cursor ?? undefined,
            signal: controller.signal,
          });
          if (
            controller.signal.aborted ||
            !trackerRef.current.isCurrentFullLoad(token)
          ) {
            return;
          }

          commit((previous) => {
            const items =
              mode === "more"
                ? mergeCursorPageItems(
                    previous.items,
                    page.items,
                    getKeyRef.current,
                  )
                : page.items;
            return {
              items,
              nextCursor: page.next_cursor,
              loading: false,
              refreshing: false,
              loadingMore: false,
              merging: false,
              error: null,
              loadMoreError: null,
              mergeError: null,
              stale: false,
              updatedAt: Date.now(),
            };
          });
        } catch (caught) {
          if (
            controller.signal.aborted ||
            !trackerRef.current.isCurrentFullLoad(token)
          ) {
            return;
          }
          const error = toApiError(caught);
          commit((previous) => {
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
            return next;
          });
        }
      })();
    },
    [commit],
  );

  useEffect(() => {
    void execute("initial");
    return () => {
      controllerRef.current?.abort();
      mergeControllerRef.current?.abort();
    };
  }, [execute]);

  const reload = useCallback(() => {
    void execute("refresh");
  }, [execute]);

  const loadMore = useCallback(() => {
    void execute("more");
  }, [execute]);

  const refreshInPlace = useCallback(() => {
    const current = stateRef.current;
    // 用户正在首屏加载/手动刷新/翻页时跳过本轮：合并刷新不得打断分页，
    // 也不叠加同一类请求（轮询间隔可能短于请求耗时）。
    if (
      current.loading ||
      current.refreshing ||
      current.loadingMore ||
      current.merging
    ) {
      return;
    }

    const token = trackerRef.current.beginMerge();
    mergeControllerRef.current?.abort();
    const controller = new AbortController();
    mergeControllerRef.current = controller;

    commit((previous) => ({
      ...previous,
      merging: true,
      mergeError: null,
    }));

    void (async () => {
      try {
        const page = await requestRef.current({ signal: controller.signal });
        if (
          controller.signal.aborted ||
          !trackerRef.current.isCurrentMerge(token)
        ) {
          return;
        }
        commit((previous) =>
          mergedRefreshSuccessState(previous, page, getKeyRef.current),
        );
      } catch (caught) {
        if (
          controller.signal.aborted ||
          !trackerRef.current.isCurrentMerge(token)
        ) {
          return;
        }
        commit((previous) =>
          mergedRefreshFailureState(previous, toApiError(caught)),
        );
      }
    })();
  }, [commit]);

  return {
    ...state,
    hasMore: state.nextCursor !== null,
    reload,
    loadMore,
    refreshInPlace,
  };
}
