import { describe, expect, it } from "vitest";

import { ApiError } from "../services/api";
import {
  createCursorPageRequestTracker,
  mergedRefreshFailureState,
  mergedRefreshSuccessState,
} from "./useCursorPage";
import type { CursorPageState } from "./useCursorPage";

interface Row {
  id: string;
  value: number;
}

function getKey(row: Row): string {
  return row.id;
}

function row(id: string, value = 1): Row {
  return { id, value };
}

function state(
  overrides: Partial<CursorPageState<Row>> = {},
): CursorPageState<Row> {
  return {
    items: [],
    nextCursor: null,
    loading: false,
    refreshing: false,
    loadingMore: false,
    merging: false,
    error: null,
    loadMoreError: null,
    mergeError: null,
    stale: false,
    updatedAt: null,
    ...overrides,
  };
}

function page(items: Row[], nextCursor: string | null = null) {
  return { items, next_cursor: nextCursor };
}

describe("原地合并刷新状态迁移", () => {
  it("已加载的后续页不被重置，顺序保持不变", () => {
    const previous = state({
      items: [row("a"), row("b"), row("c"), row("d")],
      nextCursor: "page-3",
      updatedAt: 1,
    });

    const next = mergedRefreshSuccessState(
      previous,
      page([row("a"), row("b")], "page-2"),
      getKey,
    );

    expect(next.items.map(getKey)).toEqual(["a", "b", "c", "d"]);
    // 游标跟随服务端第一页，而不是回到列表起点。
    expect(next.nextCursor).toBe("page-2");
    expect(next.merging).toBe(false);
    expect(next.stale).toBe(false);
    expect(next.updatedAt).toBeTypeOf("number");
  });

  it("变化项就地替换，不改变所在位置", () => {
    const previous = state({
      items: [row("a", 1), row("b", 1), row("c", 1)],
    });

    const next = mergedRefreshSuccessState(
      previous,
      page([row("a", 2), row("b", 3)]),
      getKey,
    );

    expect(next.items).toEqual([row("a", 2), row("b", 3), row("c", 1)]);
  });

  it("本页已不存在的项被移除", () => {
    const previous = state({
      items: [row("a"), row("b"), row("c")],
    });

    const next = mergedRefreshSuccessState(
      previous,
      page([row("a"), row("c")]),
      getKey,
    );

    expect(next.items.map(getKey)).toEqual(["a", "c"]);
  });

  it("空页保留已加载数据与游标，不闪成空态", () => {
    const previous = state({
      items: [row("a"), row("b")],
      nextCursor: "page-2",
      merging: true,
      stale: true,
    });

    const next = mergedRefreshSuccessState(previous, page([]), getKey);

    expect(next.items).toEqual([row("a"), row("b")]);
    expect(next.nextCursor).toBe("page-2");
    expect(next.merging).toBe(false);
    expect(next.stale).toBe(false);
  });
});

describe("原地合并刷新失败", () => {
  const error = new ApiError({
    kind: "http",
    status: 503,
    message: "服务暂时不可用",
  });

  it("已有数据时保留列表并标记 Stale，不清空错误位", () => {
    const previous = state({
      items: [row("a")],
      merging: true,
      error: null,
    });

    const next = mergedRefreshFailureState(previous, error);

    expect(next.items).toEqual([row("a")]);
    expect(next.merging).toBe(false);
    expect(next.mergeError).toBe(error);
    expect(next.stale).toBe(true);
    expect(next.error).toBeNull();
  });

  it("尚无数据时进入首屏错误态", () => {
    const previous = state({ merging: true });

    const next = mergedRefreshFailureState(previous, error);

    expect(next.error).toBe(error);
    expect(next.stale).toBe(false);
    expect(next.merging).toBe(false);
    expect(next.mergeError).toBe(error);
  });
});

describe("请求代数与过期响应", () => {
  it("后发起的合并刷新让先前的在途响应过期", () => {
    const tracker = createCursorPageRequestTracker();
    const first = tracker.beginMerge();
    const second = tracker.beginMerge();

    expect(tracker.isCurrentMerge(first)).toBe(false);
    expect(tracker.isCurrentMerge(second)).toBe(true);
  });

  it("全量加载会作废在途的合并刷新响应", () => {
    const tracker = createCursorPageRequestTracker();
    const merge = tracker.beginMerge();

    tracker.beginFullLoad();

    expect(tracker.isCurrentMerge(merge)).toBe(false);
  });

  it("后发起的全量加载让先前全量响应过期（手动刷新/翻页竞态）", () => {
    const tracker = createCursorPageRequestTracker();
    const first = tracker.beginFullLoad();
    const second = tracker.beginFullLoad();

    expect(tracker.isCurrentFullLoad(first)).toBe(false);
    expect(tracker.isCurrentFullLoad(second)).toBe(true);
  });

  it("合并刷新不会作废在途的加载更多响应", () => {
    const tracker = createCursorPageRequestTracker();
    const loadMore = tracker.beginFullLoad();

    tracker.beginMerge();

    expect(tracker.isCurrentFullLoad(loadMore)).toBe(true);
  });
});
