import { describe, expect, it, vi } from "vitest";

import {
  AUDIT_PROJECT_ID,
  AUDIT_RESOURCE_ID,
  auditFixture,
} from "./__fixtures__/auditLogFixtures";
import { ApiClient, ApiError } from "./api";
import { createAuditLogApi } from "./auditLogs";
import { ContractValidationError } from "./validation";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiWith(body: unknown, status = 200) {
  const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse(body, status));
  const api = createAuditLogApi(
    new ApiClient({ baseUrl: "http://api.test", fetchImpl }),
  );
  return { fetchImpl, api };
}

describe("审计日志 API client", () => {
  it("列表把过滤条件与分页参数一并下推为查询串", async () => {
    const { fetchImpl, api } = apiWith({
      items: [auditFixture()],
      next_cursor: null,
    });

    const page = await api.list(AUDIT_PROJECT_ID, {
      limit: 20,
      cursor: "abc",
      filters: {
        resource_type: "RAW_ASSET",
        action: "validate",
        result: "SUCCESS",
        occurred_from: "2026-11-01T00:00:00.000Z",
        occurred_to: "2026-11-17T23:59:59.999Z",
      },
    });

    expect(page.items).toHaveLength(1);
    // 查询串按 key 字典序拼接，便于断言与缓存命中。
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      "http://api.test/api/v1/projects/" +
        `${AUDIT_PROJECT_ID}/audit-logs?action=validate&cursor=abc` +
        "&limit=20&occurred_from=2026-11-01T00%3A00%3A00.000Z" +
        "&occurred_to=2026-11-17T23%3A59%3A59.999Z&resource_type=RAW_ASSET" +
        "&result=SUCCESS",
    );
  });

  it("资源时间线使用独立路径", async () => {
    const { fetchImpl, api } = apiWith({ items: [], next_cursor: null });

    await api.timeline(AUDIT_PROJECT_ID, "RAW_ASSET", AUDIT_RESOURCE_ID);

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${AUDIT_PROJECT_ID}/audit-logs/RAW_ASSET/${AUDIT_RESOURCE_ID}?limit=100`,
    );
  });

  it("资源类型为空时拒绝发请求", async () => {
    const { fetchImpl, api } = apiWith({ items: [], next_cursor: null });

    await expect(
      api.timeline(AUDIT_PROJECT_ID, "  ", AUDIT_RESOURCE_ID),
    ).rejects.toThrow(ContractValidationError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("响应里出现别的项目时拒绝展示", async () => {
    const { api } = apiWith({
      items: [
        auditFixture({ project_id: "99999999-9999-4999-8999-999999999999" }),
      ],
      next_cursor: null,
    });

    // API client 把契约校验失败统一包装为 invalid_response，界面按错误态展示。
    await expect(api.list(AUDIT_PROJECT_ID)).rejects.toThrow(ApiError);
  });
});
