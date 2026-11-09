/**
 * 访问令牌存储。DDP 尚未内置登录/令牌签发，前端只负责携带外部签发的
 * Bearer JWT；令牌按浏览器会话保存，不写入 URL、日志或服务端存储。
 */

const STORAGE_KEY = "ddp.access_token";

function session(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function readAccessToken(): string | null {
  const storage = session();
  if (!storage) {
    return null;
  }
  try {
    const value = storage.getItem(STORAGE_KEY);
    return value && value.trim() !== "" ? value.trim() : null;
  } catch {
    return null;
  }
}

export function storeAccessToken(token: string): string | null {
  const trimmed = token.trim();
  const storage = session();
  if (trimmed === "" || !storage) {
    return null;
  }
  try {
    storage.setItem(STORAGE_KEY, trimmed);
    return trimmed;
  } catch {
    return null;
  }
}

export function clearAccessToken(): void {
  const storage = session();
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // 浏览器禁用存储时忽略；调用方仍可继续以未认证状态浏览。
  }
}
