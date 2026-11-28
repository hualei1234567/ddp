/** 处理运行页面路由构造。 */

export function processingRunListPath(): string {
  return "/processing";
}

export function processingRunDetailPath(runId: string): string {
  return `/processing/${encodeURIComponent(runId)}`;
}
