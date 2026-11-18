import { useEffect, useRef } from "react";

import { createPollingLoop } from "../services/polling";
import type { PollingTick } from "../services/polling";

export interface UsePollingOptions {
  /** 为 false 时立即停止（并在 effect 清理中取消已排队的定时器）。 */
  enabled: boolean;
  intervalMs: number;
  tick: PollingTick;
}

/**
 * 把轮询循环绑定到 React 生命周期。
 *
 * `tick` 通过 ref 读取最新闭包：调用方每次渲染重建回调不会导致 effect
 * 反复重启，也不会在重启之间留下未清理的定时器。
 */
export function usePolling({
  enabled,
  intervalMs,
  tick,
}: UsePollingOptions): void {
  const tickRef = useRef(tick);
  tickRef.current = tick;

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    const loop = createPollingLoop({
      intervalMs,
      tick: () => tickRef.current(),
    });
    return () => {
      loop.stop();
    };
  }, [enabled, intervalMs]);
}
