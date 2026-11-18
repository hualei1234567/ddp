/** 导入日志的展示顺序：只按事实排序，不依赖分页返回顺序。 */

/** 排序只需要两个字段；用结构化类型避免把整个 ImportLog 拖进纯函数测试。 */
export interface ImportLogOrderKey {
  occurred_at: string;
  log_id: string;
}

/**
 * 按「新 → 旧」稳定排序。
 *
 * 服务端每页按时间正序返回，"加载更多"会把更早的一页追加到数组尾部；因此
 * 不能简单反转整个累积数组——那会把更早页内部的顺序也翻反。这里按
 * `occurred_at` 倒序，同一时刻用 `log_id` 倒序兜底，结果与分页顺序无关。
 */
export function sortImportLogsNewestFirst<T extends ImportLogOrderKey>(
  items: readonly T[],
): T[] {
  return [...items].sort((left, right) => {
    const delta = Date.parse(right.occurred_at) - Date.parse(left.occurred_at);
    // 解析失败得到 NaN：比较器返回 NaN 会让顺序未定义，退回 log_id 兜底。
    if (Number.isFinite(delta) && delta !== 0) {
      return delta;
    }
    return right.log_id < left.log_id ? -1 : right.log_id > left.log_id ? 1 : 0;
  });
}
