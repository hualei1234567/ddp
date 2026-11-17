/**
 * 经营分析 client 的请求层测试：路径、If-Match、请求体与响应校验。
 *
 * 覆盖任务要求的「配额编辑成功」与「409 / 403」在请求层的真实行为：
 * 前端不拦截、不隐藏，只如实把服务端结论交给页面。
 */

import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./api";
import { createGovernanceApi } from "./governance";

const PROJECT_ID = "11111111-2222-4333-8444-555555555555";
const OTHER_PROJECT_ID = "99999999-8888-4777-8666-555555555555";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

function quotaPayload(overrides: Record<string, unknown> = {}) {
  return {
    quota_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    project_id: PROJECT_ID,
    resource_kind: "EPISODES",
    limit: 5000,
    version: 2,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-17T00:00:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

function usageReportPayload() {
  return {
    project_id: PROJECT_ID,
    generated_at: "2026-11-17T02:05:00Z",
    entries: [
      {
        resource_kind: "OBJECT_STORAGE_BYTES",
        used: null,
        limit: null,
        remaining: null,
        exceeded: null,
        status: "UNMEASURED",
        measurement_source: null,
        measured_at: null,
      },
    ],
  };
}

describe("用量报表请求", () => {
  it("GET 真实路径并解析出未测量条目", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(usageReportPayload()),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    const report = await api.getUsageReport(PROJECT_ID);

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/usage-report`,
    );
    expect(report.entries[0]?.used).toBeNull();
    expect(report.entries[0]?.status).toBe("UNMEASURED");
  });

  it("响应 project_id 与请求路径不一致时拒绝（跨项目隔离）", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ ...usageReportPayload(), project_id: OTHER_PROJECT_ID }),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    await expect(api.getUsageReport(PROJECT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});

describe("配额列表请求", () => {
  it("只返回已配置配额，并保留 version 供 If-Match 使用", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [quotaPayload()] }),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    const quotas = await api.listQuotas(PROJECT_ID);

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/quotas`,
    );
    expect(quotas).toHaveLength(1);
    expect(quotas[0]?.version).toBe(2);
  });

  it("条目 project_id 与请求路径不一致时拒绝", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [quotaPayload({ project_id: OTHER_PROJECT_ID })] }),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    await expect(api.listQuotas(PROJECT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});

describe("配额写入请求", () => {
  it("修改既有配额用 PUT + If-Match，请求体只有 limit", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(quotaPayload({ limit: 8000, version: 3 })),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    const quota = await api.setQuota(PROJECT_ID, "EPISODES", 8000, {
      version: 2,
    });

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/quotas/EPISODES`,
    );
    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(init?.method).toBe("PUT");
    expect((init?.headers as Record<string, string>)["If-Match"]).toBe('"2"');
    expect(JSON.parse(String(init?.body))).toEqual({ limit: 8000 });
    // 服务端在响应体 version 里给出新版本（与 ETag 同值）。
    expect(quota.version).toBe(3);
  });

  it("新建（无既有配额）不带 If-Match", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(quotaPayload({ resource_kind: "INGESTION_JOBS", version: 1 })),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    await api.setQuota(PROJECT_ID, "INGESTION_JOBS", 100);

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect((init?.headers as Record<string, string>)["If-Match"]).toBeUndefined();
    expect(JSON.parse(String(init?.body))).toEqual({ limit: 100 });
  });

  it("409 版本冲突以 ApiError 抛出，由页面如实提示", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(
        {
          error: {
            code: "conflict",
            message: "配额版本已变化",
            request_id: "req-1",
          },
        },
        409,
      ),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    const caught = await api
      .setQuota(PROJECT_ID, "EPISODES", 8000, { version: 1 })
      .catch((error: unknown) => error);

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).isConflict).toBe(true);
  });

  it("403 不被前端拦截，抛出后由页面提示权限被拒", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(
        { error: { code: "forbidden", message: "无权修改配额" } },
        403,
      ),
    );
    const api = createGovernanceApi(clientWith(fetchImpl));

    const caught = await api
      .setQuota(PROJECT_ID, "EPISODES", 8000, { version: 1 })
      .catch((error: unknown) => error);

    expect((caught as ApiError).isForbidden).toBe(true);
  });

  it("非法上限在发请求前就被拒绝", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse({}));
    const api = createGovernanceApi(clientWith(fetchImpl));

    await expect(
      api.setQuota(PROJECT_ID, "EPISODES", -1),
    ).rejects.toThrow(RangeError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
