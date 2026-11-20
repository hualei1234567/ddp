import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "./api";
import { createCaptureSessionApi } from "./captureSessions";
import { parseCaptureSession, parseCaptureSessionArtifactPage } from "./captureSessionParsers";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const SESSION_ID = "22222222-2222-4222-8222-222222222222";

function fixture(overrides: Record<string, unknown> = {}) {
  return {
    session_id: SESSION_ID, project_id: PROJECT_ID, code: "CAP-001",
    task_id: null, batch_id: null, operator_id: null, robot_id: null,
    location_id: null, status: "SCHEDULED", started_at: null, ended_at: null,
    status_reason: null, allowed_actions: ["start", "abort"], version: 1,
    created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z",
    created_by: null, updated_by: null, ...overrides,
  };
}

function apiWith(body: unknown) {
  const fetchImpl = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" },
  }));
  return { fetchImpl, api: createCaptureSessionApi(new ApiClient({ baseUrl: "http://api.test", fetchImpl })) };
}

describe("采集会话严格解析", () => {
  it("allowed_actions 缺失时拒绝响应，空数组合法", () => {
    const { allowed_actions: _omitted, ...missing } = fixture();
    expect(_omitted).toBeDefined();
    expect(() => parseCaptureSession(missing)).toThrow(/allowed_actions/);
    expect(parseCaptureSession(fixture({ allowed_actions: [] })).allowed_actions).toEqual([]);
  });

  it("拒绝非法会话状态与非法产物类型", () => {
    expect(() => parseCaptureSession(fixture({ status: "UNKNOWN" }))).toThrow(/枚举/);
    expect(() => parseCaptureSessionArtifactPage({ items: [{ kind: "FILE", artifact_id: SESSION_ID, code: "A", status: "READY", created_at: "2026-11-20T00:00:00Z" }], next_cursor: null })).toThrow(/枚举/);
  });
});

describe("采集会话 API", () => {
  it("创建只提交可写字段并携带幂等键", async () => {
    const { api, fetchImpl } = apiWith(fixture());
    await api.create(PROJECT_ID, { code: "CAP-001", task_id: null }, { idempotencyKey: "idem-create" });
    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Idempotency-Key": "idem-create" });
    expect(JSON.parse(String(init.body))).toEqual({ code: "CAP-001", task_id: null });
  });

  it.each(["pause", "resume", "abort"] as const)("%s 在原因缺失或空白时请求前拒绝", async (action) => {
    for (const reason of [null, undefined, "   "]) {
      const { api, fetchImpl } = apiWith(fixture());

      await expect(
        api.action(PROJECT_ID, SESSION_ID, action, reason as string | null, { version: 1 }),
      ).rejects.toThrow(/原因不能为空/);
      expect(fetchImpl).not.toHaveBeenCalled();
    }
  });

  it("动作请求携带 If-Match、幂等键和去空白原因", async () => {
    const { api, fetchImpl } = apiWith(fixture({ status: "PAUSED" }));
    await api.action(PROJECT_ID, SESSION_ID, "pause", "  现场暂停  ", { version: 1, idempotencyKey: "idem-1" });
    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(`http://api.test/api/v1/projects/${PROJECT_ID}/capture-sessions/${SESSION_ID}/actions/pause`);
    expect(init.headers).toMatchObject({ "If-Match": "\"1\"", "Idempotency-Key": "idem-1" });
    expect(JSON.parse(String(init.body))).toEqual({ reason: "现场暂停" });
  });

  it("产物使用独立游标端点", async () => {
    const { api, fetchImpl } = apiWith({ items: [], next_cursor: null });
    await api.listArtifacts(PROJECT_ID, SESSION_ID, { cursor: "next" });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(`http://api.test/api/v1/projects/${PROJECT_ID}/capture-sessions/${SESSION_ID}/artifacts?cursor=next&limit=100`);
  });
});
