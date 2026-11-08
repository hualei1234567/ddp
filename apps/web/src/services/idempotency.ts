/**
 * 写动作幂等键。作用域由服务端按 `(project_id, user_id, key)` 判定；
 * 前端只保证同一逻辑操作在重试时复用同一个键，不把键当作权限或状态依据。
 */
export function createIdempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `idem-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 12)}`;
}

/** OpenAPI IfMatch 规则：`"<正整数版本>"`。 */
export function formatIfMatch(version: number): string {
  if (!Number.isInteger(version) || version < 1) {
    throw new Error("If-Match 版本号必须是正整数");
  }
  return `"${version}"`;
}
