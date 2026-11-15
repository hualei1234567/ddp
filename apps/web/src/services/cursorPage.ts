/**
 * 游标分页的纯数据合并逻辑，供 `useCursorPage` 与测试复用。
 *
 * 服务端游标可能因并发写入出现重复项，前端追加时按业务主键去重，
 * 避免 React 列表 key 冲突和重复展示。
 */
export function mergeCursorPageItems<T>(
  existing: readonly T[],
  incoming: readonly T[],
  getKey: (item: T) => string,
): T[] {
  const seen = new Set(existing.map(getKey));
  const merged = [...existing];

  for (const item of incoming) {
    const key = getKey(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(item);
  }

  return merged;
}

/** 单页上限（与契约 §4.1 一致）；聚合型调用点按该值逐页拉取。 */
export const CURSOR_PAGE_LIMIT = 100;

/**
 * 聚合型调用点的保护上限：最多翻多少页。
 *
 * 队列与工作台必须看到**全量**提交才能给出正确结论（见 ``annotationQueues``
 * 与 ``annotationWorkbench``）；但无限翻页会让异常数据把浏览器拖死。
 * 这里用显式上限 + 抛错代替静默截断——宁可展示 Partial/错误，也不给出
 * 一份看起来完整其实少了几十条的队列。
 */
export const MAX_AGGREGATED_PAGES = 50;

export class CursorPageOverflowError extends Error {
  constructor(readonly pageLimit: number) {
    super(`数据超过 ${pageLimit} 页上限，已停止翻页以避免静默截断`);
    this.name = "CursorPageOverflowError";
  }
}

export interface FetchPage<T> {
  (options: { cursor?: string; limit: number }): Promise<{
    items: T[];
    next_cursor: string | null;
  }>;
}

/**
 * 逐页拉完全部结果（按游标推进），返回累积条目。
 *
 * 与 ``mergeCursorPageItems`` 一样按业务主键去重：并发写入时相邻两页可能
 * 出现同一条，去重避免下游按 key 聚合时重复计数。
 */
export async function fetchAllCursorPages<T>(
  fetchPage: FetchPage<T>,
  getKey: (item: T) => string,
  options: { limit?: number; maxPages?: number } = {},
): Promise<T[]> {
  const limit = options.limit ?? CURSOR_PAGE_LIMIT;
  const maxPages = options.maxPages ?? MAX_AGGREGATED_PAGES;
  let cursor: string | undefined;
  let collected: T[] = [];

  for (let page = 0; page < maxPages; page += 1) {
    const result = await fetchPage({ cursor, limit });
    collected = mergeCursorPageItems(collected, result.items, getKey);
    if (result.next_cursor === null) {
      return collected;
    }
    cursor = result.next_cursor;
  }
  throw new CursorPageOverflowError(maxPages);
}
