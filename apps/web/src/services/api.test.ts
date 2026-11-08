import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8" },
    ...init,
  });
}

function asFetch(
  handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
): typeof fetch {
  return vi.fn(handler) as unknown as typeof fetch;
}

const echoValidator = (value: unknown): { ok: boolean } => {
  if (typeof value !== "object" || value === null || !("ok" in value)) {
    throw new Error("响应缺少 ok 字段");
  }
  return value as { ok: boolean };
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ApiClient", () => {
  it("解析成功响应并执行结构校验", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ ok: true }),
    );
    const client = new ApiClient({
      fetchImpl,
    });

    await expect(
      client.get("/demo", { validate: echoValidator }),
    ).resolves.toEqual({ ok: true });

    const call = fetchImpl.mock.calls[0];
    expect(call?.[0]).toBe("/demo");
    const init = call?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("GET");
    const headers = init?.headers as Record<string, string> | undefined;
    expect(headers).toMatchObject({ Accept: "application/json" });
    expect(headers?.["X-Request-Id"]).toBeTruthy();
  });

  it("拼接 baseUrl 与排序后的查询参数", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ ok: true }),
    );
    const client = new ApiClient({
      baseUrl: "http://api.test/",
      fetchImpl,
    });

    await client.get("/demo", {
      query: { z: 1, a: "x", skip: undefined, empty: null },
      validate: echoValidator,
    });

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      "http://api.test/demo?a=x&z=1",
    );
  });

  it("解析统一错误体并保留 code 与 request_id", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "forbidden",
            message: "无权访问",
            request_id: "rid-1",
          },
        },
        { status: 403 },
      ),
    );
    const client = new ApiClient({
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(client.get("/secret")).rejects.toMatchObject({
      kind: "http",
      status: 403,
      code: "forbidden",
      requestId: "rid-1",
      message: "无权访问",
    });
  });

  it("非 JSON 错误响应使用 HTTP 兜底文案", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response("Bad Gateway", {
          status: 502,
          headers: { "Content-Type": "text/plain" },
        }),
    );
    const client = new ApiClient({
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(client.get("/broken")).rejects.toMatchObject({
      kind: "http",
      status: 502,
      code: "http_502",
      message: "Bad Gateway",
    });
  });

  it("网络异常统一为 network", async () => {
    const fetchImpl = asFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const client = new ApiClient({ fetchImpl });

    await expect(client.get("/offline")).rejects.toMatchObject({
      kind: "network",
    });
  });

  it("请求超时抛出 timeout", async () => {
    vi.useFakeTimers();
    const fetchImpl = asFetch(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const client = new ApiClient({ fetchImpl, timeoutMs: 50 });

    const request = client.get("/slow");
    const assertion = expect(request).rejects.toMatchObject({
      kind: "timeout",
    });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;
  });

  it("外部取消标记为 aborted", async () => {
    const fetchImpl = asFetch(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const client = new ApiClient({ fetchImpl });
    const controller = new AbortController();

    const request = client.get("/slow", { signal: controller.signal });
    const assertion = expect(request).rejects.toMatchObject({
      kind: "aborted",
    });
    controller.abort();
    await assertion;
  });

  it("校验失败标记为 invalid_response", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ unexpected: true }));
    const client = new ApiClient({
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(
      client.get("/bad", {
        validate: () => {
          throw new Error("契约不匹配");
        },
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("204 空响应返回 undefined", async () => {
    const fetchImpl = vi.fn(
      async () => new Response(null, { status: 204 }),
    );
    const client = new ApiClient({
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    await expect(client.get("/empty")).resolves.toBeUndefined();
  });

  it("错误类型提供 isForbidden 判断", () => {
    const error = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问",
    });
    expect(error.isForbidden).toBe(true);
    expect(error.isUnauthorized).toBe(false);
  });
});
