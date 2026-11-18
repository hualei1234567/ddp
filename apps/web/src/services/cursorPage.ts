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

export interface CursorPageRefreshResult<T> {
  items: T[];
  /** 本次刷新判定为「已不存在」的主键；只覆盖观察窗口内的条目。 */
  removedKeys: string[];
}

/**
 * 原地合并刷新：用服务端当前第一页合并已加载列表，不重置分页。
 *
 * 与 ``mergeCursorPageItems``（追加翻页）不同，本函数面向轮询/自动刷新：
 *
 * - 已加载条目的**位置不变**；同主键用新对象就地替换，避免轮询时行跳动。
 * - 新增条目按服务端当前顺序插入：以服务端顺序中最近的已落位条目为锚点，
 *   优先插在它后面，其次插在它前面，都没有锚点时插在列表最前。
 * - 删除只判定在「观察窗口」内。窗口是已加载列表中直到最后一个出现在本页的
 *   条目为止的前缀；窗口内不在本页的条目视为已不存在并移除。窗口之外的尾部
 *   条目本次没有被观察到（可能只是被新数据挤到了下一页），一律保留，
 *   避免把已加载的分页误删成空洞。
 * - 空页不当作「全部删除」：保留现有列表，权威清空交给 ``reload`` 全量刷新。
 *
 * 已知取舍：窗口外的删除（例如第 2 页上的条目被删）本次不会被发现，
 * 需要手动刷新才收敛；这是只拉第一页的代价，换取翻页位置不丢失。
 */
export function mergeCursorPageRefresh<T>(
  existing: readonly T[],
  incoming: readonly T[],
  getKey: (item: T) => string,
): CursorPageRefreshResult<T> {
  if (incoming.length === 0) {
    return { items: [...existing], removedKeys: [] };
  }

  const freshByKey = new Map<string, T>();
  const freshOrder: string[] = [];
  for (const item of incoming) {
    const key = getKey(item);
    if (freshByKey.has(key)) {
      continue;
    }
    freshByKey.set(key, item);
    freshOrder.push(key);
  }

  const replaced = existing.map((item) => freshByKey.get(getKey(item)) ?? item);

  // 观察窗口右边界：已加载列表中最后一个出现在本页的条目下标。
  let windowEnd = -1;
  for (let index = 0; index < existing.length; index += 1) {
    const item = existing[index];
    if (item !== undefined && freshByKey.has(getKey(item))) {
      windowEnd = index;
    }
  }

  const removedKeys: string[] = [];
  const items: T[] = [];
  for (let index = 0; index < replaced.length; index += 1) {
    const item = replaced[index];
    if (item === undefined) {
      continue;
    }
    const key = getKey(item);
    if (index <= windowEnd && !freshByKey.has(key)) {
      removedKeys.push(key);
      continue;
    }
    items.push(item);
  }

  const positions = new Map<string, number>();
  items.forEach((item, index) => positions.set(getKey(item), index));

  // 倒序处理新条目，让同一批新增项按服务端顺序落位。
  for (let order = freshOrder.length - 1; order >= 0; order -= 1) {
    const key = freshOrder[order];
    if (key === undefined || positions.has(key)) {
      continue;
    }
    const item = freshByKey.get(key);
    if (item === undefined) {
      continue;
    }

    let insertAt: number | null = null;
    for (let after = order + 1; after < freshOrder.length; after += 1) {
      const anchorKey = freshOrder[after];
      const anchor =
        anchorKey === undefined ? undefined : positions.get(anchorKey);
      if (anchor !== undefined) {
        insertAt = anchor;
        break;
      }
    }
    if (insertAt === null) {
      for (let before = order - 1; before >= 0; before -= 1) {
        const anchorKey = freshOrder[before];
        const anchor =
          anchorKey === undefined ? undefined : positions.get(anchorKey);
        if (anchor !== undefined) {
          insertAt = anchor + 1;
          break;
        }
      }
    }

    const target = insertAt ?? 0;
    items.splice(target, 0, item);
    for (let index = target; index < items.length; index += 1) {
      const current = items[index];
      if (current !== undefined) {
        positions.set(getKey(current), index);
      }
    }
  }

  return { items, removedKeys };
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
