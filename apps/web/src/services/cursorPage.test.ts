import { describe, expect, it, vi } from "vitest";

import {
  CURSOR_PAGE_LIMIT,
  CursorPageOverflowError,
  fetchAllCursorPages,
  mergeCursorPageItems,
} from "./cursorPage";

interface Row {
  id: string;
  value: number;
}

function getKey(row: Row): string {
  return row.id;
}

describe("游标分页合并", () => {
  it("追加下一页并保持已加载顺序", () => {
    const existing: Row[] = [
      { id: "a", value: 1 },
      { id: "b", value: 2 },
    ];
    const incoming: Row[] = [
      { id: "c", value: 3 },
      { id: "d", value: 4 },
    ];

    expect(mergeCursorPageItems(existing, incoming, getKey)).toEqual([
      { id: "a", value: 1 },
      { id: "b", value: 2 },
      { id: "c", value: 3 },
      { id: "d", value: 4 },
    ]);
  });

  it("按业务主键去重，保留已加载项", () => {
    const existing: Row[] = [{ id: "a", value: 1 }];
    const incoming: Row[] = [
      { id: "a", value: 99 },
      { id: "b", value: 2 },
    ];

    expect(mergeCursorPageItems(existing, incoming, getKey)).toEqual([
      { id: "a", value: 1 },
      { id: "b", value: 2 },
    ]);
  });

  it("同一页内重复主键也只保留第一项", () => {
    const incoming: Row[] = [
      { id: "a", value: 1 },
      { id: "a", value: 2 },
    ];

    expect(mergeCursorPageItems<Row>([], incoming, getKey)).toEqual([
      { id: "a", value: 1 },
    ]);
  });

  it("空页不改变已加载数据", () => {
    const existing: Row[] = [{ id: "a", value: 1 }];

    expect(mergeCursorPageItems(existing, [], getKey)).toEqual(existing);
  });

  it("不修改入参数组", () => {
    const existing: Row[] = [{ id: "a", value: 1 }];
    const incoming: Row[] = [{ id: "b", value: 2 }];

    mergeCursorPageItems(existing, incoming, getKey);

    expect(existing).toEqual([{ id: "a", value: 1 }]);
    expect(incoming).toEqual([{ id: "b", value: 2 }]);
  });
});

describe("聚合型全量翻页", () => {
  it("按游标逐页拉全量，顺序与主键去重都正确", async () => {
    const pages: Record<string, Row[]> = {
      start: [
        { id: "a", value: 1 },
        { id: "b", value: 2 },
      ],
      c2: [
        { id: "c", value: 3 },
        { id: "d", value: 4 },
      ],
    };
    const seenCursors: Array<string | undefined> = [];
    const fetchPage = vi.fn(
      async (options: { cursor?: string; limit: number }) => {
        seenCursors.push(options.cursor);
        const key = options.cursor ?? "start";
        return {
          items: pages[key] ?? [],
          next_cursor: key === "start" ? "c2" : null,
        };
      },
    );

    await expect(fetchAllCursorPages(fetchPage, getKey)).resolves.toEqual([
      { id: "a", value: 1 },
      { id: "b", value: 2 },
      { id: "c", value: 3 },
      { id: "d", value: 4 },
    ]);
    expect(seenCursors).toEqual([undefined, "c2"]);
    expect(fetchPage.mock.calls[0]?.[0].limit).toBe(CURSOR_PAGE_LIMIT);
  });

  it("跨页重复主键只保留第一次出现", async () => {
    const responses = [
      { items: [{ id: "a", value: 1 }], next_cursor: "p2" },
      {
        items: [
          { id: "a", value: 99 },
          { id: "b", value: 2 },
        ],
        next_cursor: null,
      },
    ];
    let index = 0;
    const fetchPage = async () => responses[index++] ?? {
      items: [],
      next_cursor: null,
    };

    await expect(fetchAllCursorPages(fetchPage, getKey)).resolves.toEqual([
      { id: "a", value: 1 },
      { id: "b", value: 2 },
    ]);
  });

  it("超过页数上限时抛错，不静默返回部分结果", async () => {
    const fetchPage = vi.fn(async () => ({
      items: [{ id: "a", value: 1 }],
      next_cursor: "always-more",
    }));

    await expect(
      fetchAllCursorPages(fetchPage, getKey, { maxPages: 3 }),
    ).rejects.toBeInstanceOf(CursorPageOverflowError);
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it("首页即末页时只发一次请求", async () => {
    const fetchPage = vi.fn(async () => ({
      items: [{ id: "a", value: 1 }],
      next_cursor: null,
    }));

    await fetchAllCursorPages(fetchPage, getKey);

    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
