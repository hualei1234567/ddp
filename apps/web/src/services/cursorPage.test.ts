import { describe, expect, it } from "vitest";

import { mergeCursorPageItems } from "./cursorPage";

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
