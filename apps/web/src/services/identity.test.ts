import { describe, expect, it } from "vitest";

import { ApiClient } from "./api";
import { createIdentityApi, parseCurrentUser } from "./identity";

const USER_ID = "11111111-1111-1111-1111-111111111111";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(handler: (url: string) => Response): {
  client: ApiClient;
  urls: string[];
} {
  const urls: string[] = [];
  const client = new ApiClient({
    baseUrl: "http://127.0.0.1:8000",
    fetchImpl: async (input) => {
      urls.push(String(input));
      return handler(String(input));
    },
  });
  return { client, urls };
}

describe("parseCurrentUser", () => {
  it("严格解析 uuid / 显示名 / 平台角色", () => {
    expect(
      parseCurrentUser({
        user_id: USER_ID,
        display_name: "张三",
        platform_role: "member",
      }),
    ).toEqual({
      user_id: USER_ID,
      display_name: "张三",
      platform_role: "member",
    });
  });

  it("未知平台角色直接失败，不按普通成员放行", () => {
    expect(() =>
      parseCurrentUser({
        user_id: USER_ID,
        display_name: "张三",
        platform_role: "superuser",
      }),
    ).toThrow(/platform_role/);
  });

  it("user_id 非 UUID 时拒绝，避免用错误身份拼请求", () => {
    expect(() =>
      parseCurrentUser({
        user_id: "not-a-uuid",
        display_name: "张三",
        platform_role: "member",
      }),
    ).toThrow(/user_id/);
  });
});

describe("createIdentityApi", () => {
  it("调用 /api/v1/me 并返回解析后的主体", async () => {
    const { client, urls } = clientWith(() =>
      jsonResponse({
        user_id: USER_ID,
        display_name: "张三",
        platform_role: "admin",
      }),
    );

    const me = await createIdentityApi(client).getCurrentUser();

    expect(urls).toEqual(["http://127.0.0.1:8000/api/v1/me"]);
    expect(me.platform_role).toBe("admin");
  });

  it("用户目录缺失（404）时抛出 ApiError 而不是伪造身份", async () => {
    const { client } = clientWith(() =>
      jsonResponse(
        {
          error: {
            code: "not_found",
            message: "当前用户不在用户目录中",
            request_id: "req-1",
          },
        },
        404,
      ),
    );

    await expect(createIdentityApi(client).getCurrentUser()).rejects.toThrow(
      "当前用户不在用户目录中",
    );
  });
});
