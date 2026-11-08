import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../services/api";

export interface RequestState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refreshing: boolean;
  stale: boolean;
  updatedAt: number | null;
}

export interface UseRequestResult<T> extends RequestState<T> {
  reload: () => void;
}

const INITIAL_STATE = {
  data: null,
  error: null,
  loading: true,
  refreshing: false,
  stale: false,
  updatedAt: null,
} satisfies RequestState<never>;

export function requestSuccessState<T>(data: T): RequestState<T> {
  return {
    data,
    error: null,
    loading: false,
    refreshing: false,
    stale: false,
    updatedAt: Date.now(),
  };
}

/** 刷新失败时保留旧数据并标记 Stale；首次失败没有旧数据可保留。 */
export function requestFailureState<T>(
  previous: RequestState<T>,
  error: ApiError,
): RequestState<T> {
  return {
    ...previous,
    loading: false,
    refreshing: false,
    error,
    stale: previous.data !== null,
  };
}

/**
 * 轻量请求状态机：首次加载、手动刷新、取消、刷新失败保留旧数据（Stale）。
 * 只负责状态，不承担数据转换；业务聚合放在 services。
 */
export function useRequest<T>(
  request: (signal: AbortSignal) => Promise<T>,
): UseRequestResult<T> {
  const [state, setState] = useState<RequestState<T>>(INITIAL_STATE);
  const requestRef = useRef(request);
  const controllerRef = useRef<AbortController | null>(null);

  requestRef.current = request;

  const execute = useCallback(async (mode: "initial" | "refresh") => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    setState((previous) => ({
      ...previous,
      loading: mode === "initial" && previous.data === null,
      refreshing: mode === "refresh",
    }));

    try {
      const data = await requestRef.current(controller.signal);
      if (controller.signal.aborted) {
        return;
      }
      setState(requestSuccessState(data));
    } catch (caught) {
      if (controller.signal.aborted) {
        return;
      }
      const error =
        caught instanceof ApiError
          ? caught
          : new ApiError({
              kind: "network",
              message: "请求失败，请检查网络或服务状态。",
              cause: caught,
            });
      setState((previous) => requestFailureState(previous, error));
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

  return { ...state, reload };
}
