/**
 * 采集素材页面的 URL 状态与路径构造。
 *
 * 只读写白名单参数：`status` 回到服务端过滤；其余筛选由页面基于已加载数据
 * 计算时必须标注作用域。
 */

import {
  COLLECTION_ASSET_STATUS_VALUES,
  type CollectionAssetKind,
  type CollectionAssetStatus,
} from "../types/collectionAsset";

export const COLLECTION_ASSET_LIST_ROOT = "/collection-assets";

export function assetListPath(kind: CollectionAssetKind): string {
  return `${COLLECTION_ASSET_LIST_ROOT}/${kind}`;
}

export function assetDetailPath(
  kind: CollectionAssetKind,
  assetId: string,
): string {
  return `${assetListPath(kind)}/${encodeURIComponent(assetId)}`;
}

/** URL 里的未知状态值直接忽略，不把它当作合法过滤条件发给服务端。 */
export function readAssetStatusFilter(
  params: URLSearchParams,
): CollectionAssetStatus | undefined {
  const value = (params.get("status") ?? "").trim();
  return COLLECTION_ASSET_STATUS_VALUES.find((item) => item === value);
}
