import { describe, expect, it, vi } from "vitest";

import {
  CURSOR_PAGE_LIMIT,
  CursorPageOverflowError,
  fetchAllCursorPages,
  mergeCursorPageItems,
  mergeCursorPageRefresh,
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

describe("原地合并刷新", () => {
  it("保留已加载的后续页，不把列表重置为第一页", () => {
    const existing: Row[] = [
      { id: "a", value: 1 },
      { id: "b", value: 1 },
      { id: "c", value: 1 },
      { id: "d", value: 1 },
    ];

    const result = mergeCursorPageRefresh(
      existing,
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
      ],
      getKey,
    );

    expect(result.items).toEqual(existing);
    expect(result.removedKeys).toEqual([]);
  });

  it("同主键用新对象就地替换，位置不变", () => {
    const result = mergeCursorPageRefresh(
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
        { id: "c", value: 1 },
      ],
      [
        { id: "a", value: 2 },
        { id: "b", value: 3 },
      ],
      getKey,
    );

    expect(result.items).toEqual([
      { id: "a", value: 2 },
      { id: "b", value: 3 },
      { id: "c", value: 1 },
    ]);
    expect(result.removedKeys).toEqual([]);
  });

  it("观察窗口内已不存在的条目被移除", () => {
    const result = mergeCursorPageRefresh(
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
        { id: "c", value: 1 },
      ],
      [
        { id: "a", value: 1 },
        { id: "c", value: 1 },
      ],
      getKey,
    );

    expect(result.items.map(getKey)).toEqual(["a", "c"]);
    expect(result.removedKeys).toEqual(["b"]);
  });

  it("窗口之外的尾部条目即使不在本页也保留", () => {
    // 服务端第一页只有 a、b；c、d 已加载但本次未被观察，不能假定已删除。
    const result = mergeCursorPageRefresh(
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
        { id: "c", value: 1 },
        { id: "d", value: 1 },
      ],
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
      ],
      getKey,
    );

    expect(result.items.map(getKey)).toEqual(["a", "b", "c", "d"]);
  });

  it("新增条目按服务端当前顺序插入", () => {
    const result = mergeCursorPageRefresh(
      [
        { id: "a", value: 1 },
        { id: "b", value: 1 },
        { id: "c", value: 1 },
      ],
      [
        { id: "x", value: 1 },
        { id: "a", value: 1 },
        { id: "b", value: 1 },
      ],
      getKey,
    );

    expect(result.items.map(getKey)).toEqual(["x", "a", "b", "c"]);
  });

  it("本页全是新条目时按服务端顺序插到最前", () => {
    const result = mergeCursorPageRefresh(
      [{ id: "a", value: 1 }],
      [
        { id: "x", value: 1 },
        { id: "y", value: 1 },
      ],
      getKey,
    );

    expect(result.items.map(getKey)).toEqual(["x", "y", "a"]);
  });

  it("空页保留现有数据，不做全量删除", () => {
    const existing: Row[] = [{ id: "a", value: 1 }];

    const result = mergeCursorPageRefresh(existing, [], getKey);

    expect(result.items).toEqual(existing);
    expect(result.removedKeys).toEqual([]);
  });

  it("已加载为空时直接采用本页顺序，同页重复主键只保留第一项", () => {
    const result = mergeCursorPageRefresh(
      [],
      [
        { id: "a", value: 1 },
        { id: "a", value: 2 },
        { id: "b", value: 3 },
      ],
      getKey,
    );

    expect(result.items).toEqual([
      { id: "a", value: 1 },
      { id: "b", value: 3 },
    ]);
  });

  it("不修改入参数组", () => {
    const existing: Row[] = [{ id: "a", value: 1 }];
    const incoming: Row[] = [
      { id: "x", value: 2 },
      { id: "a", value: 3 },
    ];

    mergeCursorPageRefresh(existing, incoming, getKey);

    expect(existing).toEqual([{ id: "a", value: 1 }]);
    expect(incoming).toEqual([
      { id: "x", value: 2 },
      { id: "a", value: 3 },
    ]);
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
