/**
 * 可取消的轮询循环：只用 `setTimeout` 递归调度，不用 `setInterval`。
 *
 * 为什么不用 `setInterval`：请求耗时可能超过间隔，`setInterval` 会叠加并发
 * 请求，且离开页面时若没拿到句柄清理，定时器会继续在后台空转。这里返回
 * 显式 `stop()`；React 侧在 effect 清理函数里调用，组件卸载即停止。
 */

export type PollingTick = () => boolean | void | Promise<boolean | void>;

export interface PollingLoopOptions {
  intervalMs: number;
  /** 返回 `false` 表示到达终态，循环自行停止。 */
  tick: PollingTick;
}

export interface PollingLoop {
  stop: () => void;
}

export function createPollingLoop({
  intervalMs,
  tick,
}: PollingLoopOptions): PollingLoop {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let inFlight = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const schedule = (): void => {
    if (!stopped) {
      timer = setTimeout(run, intervalMs);
    }
  };

  async function run(): Promise<void> {
    timer = null;
    if (stopped || inFlight) {
      return;
    }
    inFlight = true;
    let keepGoing = true;
    try {
      keepGoing = (await tick()) !== false;
    } catch (error) {
      // 单次轮询失败不应终止自动刷新；请求状态 Hook 会把失败标记为 Stale。
      console.error("自动轮询失败", error);
    } finally {
      inFlight = false;
    }
    if (!stopped && keepGoing) {
      schedule();
    }
  }

  schedule();

  return {
    stop(): void {
      stopped = true;
      clearTimer();
    },
  };
}
