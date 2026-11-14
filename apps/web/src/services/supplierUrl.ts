/** 阶段 6 页面路由构造。 */

export function supplierListPath(): string {
  return "/suppliers";
}

export function supplierDetailPath(supplierId: string): string {
  return `/suppliers/${encodeURIComponent(supplierId)}`;
}

export function batchListPath(): string {
  return "/inbound-batches";
}

export function batchDetailPath(batchId: string): string {
  return `/inbound-batches/${encodeURIComponent(batchId)}`;
}

export function segmentPath(batchId: string, segmentId: string): string {
  return `/inbound-batches/${encodeURIComponent(batchId)}/segments/${encodeURIComponent(segmentId)}`;
}

export function ledgerPath(): string {
  return "/acceptance-ledger";
}
