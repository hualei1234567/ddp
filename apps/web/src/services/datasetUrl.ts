/** 阶段 4 页面路由构造；集中管理以免页面散落字符串。 */

export function datasetListPath(): string {
  return "/datasets";
}

/** 数据集详情页携带的 Episode 过滤参数（"加入数据集"的预填入口）。 */
export const DATASET_EPISODE_IDS_PARAM = "episode_ids";

function uniqueIds(ids: readonly string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of ids) {
    const id = raw.trim();
    if (id !== "" && !seen.has(id)) {
      seen.add(id);
      result.push(id);
    }
  }
  return result;
}

export function datasetDetailPath(
  datasetId: string,
  episodeIds: readonly string[] = [],
): string {
  const base = `/datasets/${encodeURIComponent(datasetId)}`;
  const ids = uniqueIds(episodeIds);
  if (ids.length === 0) {
    return base;
  }
  const params = new URLSearchParams();
  params.set(DATASET_EPISODE_IDS_PARAM, ids.join(","));
  return `${base}?${params.toString()}`;
}

/** 读回 `episode_ids`（逗号分隔），空值与重复项一律丢弃。 */
export function readEpisodeIdsParam(search: URLSearchParams): string[] {
  const raw = search.get(DATASET_EPISODE_IDS_PARAM);
  return raw ? uniqueIds(raw.split(",")) : [];
}

/**
 * 版本创建的 `selection_filters` 片段。字段名与后端
 * `DatasetSelectionFilters` 的 `episode_ids` 保持一致，规则权威实现只在后端。
 */
export function buildEpisodeSelectionFilters(
  episodeIds: readonly string[],
): Record<string, unknown> {
  return { episode_ids: uniqueIds(episodeIds) };
}

export function datasetVersionPath(datasetId: string, versionId: string): string {
  return `/datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}`;
}

export function exportListPath(): string {
  return "/exports";
}

export function exportDetailPath(exportJobId: string): string {
  return `/exports/${encodeURIComponent(exportJobId)}`;
}

export function trainingRequestListPath(): string {
  return "/training-requests";
}

export function trainingRequestDetailPath(requestId: string): string {
  return `/training-requests/${encodeURIComponent(requestId)}`;
}

export function trainingDeliveryDetailPath(deliveryId: string): string {
  return `/training-deliveries/${encodeURIComponent(deliveryId)}`;
}
