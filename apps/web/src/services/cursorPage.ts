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
