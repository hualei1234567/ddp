import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createPollingLoop } from "./polling";

describe("轮询循环", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("按固定间隔触发，stop 后不再触发", async () => {
    const tick = vi.fn();
    const loop = createPollingLoop({ intervalMs: 7_000, tick });

    expect(tick).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(7_000);
    expect(tick).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(7_000);
    expect(tick).toHaveBeenCalledTimes(2);

    loop.stop();
    await vi.advanceTimersByTimeAsync(21_000);
    expect(tick).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stop 能取消尚未触发的首个定时器（离开页面即停）", async () => {
    const tick = vi.fn();
    const loop = createPollingLoop({ intervalMs: 7_000, tick });

    loop.stop();
    await vi.advanceTimersByTimeAsync(21_000);

    expect(tick).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("tick 返回 false 表示进入终态，循环自行停止", async () => {
    const tick = vi.fn().mockResolvedValue(false);
    createPollingLoop({ intervalMs: 7_000, tick });

    await vi.advanceTimersByTimeAsync(7_000);
    expect(tick).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(70_000);
    expect(tick).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("上一次 tick 未完成时不叠加下一次请求", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tick = vi.fn(() => gate);
    createPollingLoop({ intervalMs: 1_000, tick });

    await vi.advanceTimersByTimeAsync(1_000);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(tick).toHaveBeenCalledTimes(1);

    release();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(tick).toHaveBeenCalledTimes(2);
  });
});
