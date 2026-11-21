/**
 * 当前认证主体（`GET /api/v1/me`）。
 *
 * 为什么需要服务端回答：浏览器只持有外部签发的 Bearer 令牌，读不到其中的
 * 用户 ID；「我的任务」「我的工作量」这类视图若在前端猜身份，就会把别人的
 * 队列当成自己的。本模块只做两件事——请求与严格解析，**不缓存**身份，
 * 也不提供任何"默认用户"回退。
 */

import { apiClient, type ApiClient } from "./api";
import { asRecord, readEnum, readString, readUuid } from "./validation";

export const PLATFORM_ROLE_VALUES = ["member", "admin"] as const;
export type PlatformRole = (typeof PLATFORM_ROLE_VALUES)[number];

export interface CurrentUser {
  user_id: string;
  display_name: string;
  platform_role: PlatformRole;
}

/** 严格的响应解析：未知角色与非法 UUID 一律失败，不降级成"普通成员"。 */
export function parseCurrentUser(value: unknown): CurrentUser {
  const record = asRecord(value, "当前用户");
  return {
    user_id: readUuid(record, "user_id", "当前用户.user_id"),
    display_name: readString(record, "display_name", "当前用户.display_name"),
    platform_role: readEnum(
      record,
      "platform_role",
      "当前用户.platform_role",
      PLATFORM_ROLE_VALUES,
    ),
  };
}

export interface IdentityApi {
  /** 读取已认证主体；服务端认证结果是唯一来源，不接受客户端传入的 ID。 */
  getCurrentUser: (options?: { signal?: AbortSignal }) => Promise<CurrentUser>;
}

export function createIdentityApi(client: ApiClient): IdentityApi {
  return {
    async getCurrentUser(options) {
      return client.get("/api/v1/me", {
        signal: options?.signal,
        validate: parseCurrentUser,
      });
    },
  };
}

export const identityApi = createIdentityApi(apiClient);
