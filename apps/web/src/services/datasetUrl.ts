/** 阶段 4 页面路由构造；集中管理以免页面散落字符串。 */

export function datasetListPath(): string {
  return "/datasets";
}

export function datasetDetailPath(datasetId: string): string {
  return `/datasets/${encodeURIComponent(datasetId)}`;
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
