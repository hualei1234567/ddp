import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./api";
import {
  collectionBatchFixture,
  collectionTaskFixture,
  episodeFixture,
  OTHER_PROJECT_ID,
  PROJECT_ID,
  TASK_ID,
} from "./__fixtures__/collectionFixtures";
import { createCollectionsApi } from "./collections";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", fetchImpl });
}

describe("阶段 2 采集 API client", () => {
  it("任务列表按服务端参数请求并校验分页结构", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        items: [collectionTaskFixture],
        next_cursor: "next-1",
      }),
    );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    const page = await api.listTasks(PROJECT_ID, {
      status: "IN_PROGRESS",
      assignedTo: "88888888-8888-7888-8888-888888888888",
    });

    expect(page.next_cursor).toBe("next-1");
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-tasks?assigned_to=88888888-8888-7888-8888-888888888888&limit=100&status=IN_PROGRESS`,
    );
  });

  it("任务详情路径对 ID 编码并校验项目归属", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(collectionTaskFixture),
    );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await expect(api.getTask(PROJECT_ID, TASK_ID)).resolves.toMatchObject({
      task_id: TASK_ID,
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-tasks/${TASK_ID}`,
    );
  });

  it("跨项目响应被拒绝为 invalid_response", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ ...collectionTaskFixture, project_id: OTHER_PROJECT_ID }),
    );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await expect(api.getTask(PROJECT_ID, TASK_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("任务动作带 If-Match、Idempotency-Key 与原因", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(collectionTaskFixture),
    );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await api.actOnTask(PROJECT_ID, TASK_ID, "pause", {
      version: 7,
      reason: "设备电量不足",
      idempotencyKey: "idem-key-123",
    });

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-tasks/${TASK_ID}/actions/pause`,
    );
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "If-Match": '"7"',
      "Idempotency-Key": "idem-key-123",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      reason: "设备电量不足",
    });
  });

  it("批次与 Episode 列表按任务过滤", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ items: [collectionBatchFixture], next_cursor: null }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [episodeFixture], next_cursor: null }),
      );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await api.listBatches(PROJECT_ID, TASK_ID);
    await api.listEpisodes(PROJECT_ID, {
      status: "READY",
      collectionTaskId: TASK_ID,
    });

    expect(fetchImpl.mock.calls[0]?.[0]).toContain(
      `/collection-tasks/${TASK_ID}/batches?limit=100`,
    );
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/episodes?collection_task_id=${TASK_ID}&limit=100&status=READY`,
    );
  });

  it("403 保持统一 ApiError，页面可映射 Forbidden", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "project_scope_denied",
            message: "非项目成员",
            request_id: "rid-403",
          },
        },
        403,
      ),
    );
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await expect(api.listTasks(PROJECT_ID)).rejects.toBeInstanceOf(ApiError);
    await expect(api.listTasks(PROJECT_ID)).rejects.toMatchObject({
      status: 403,
      code: "project_scope_denied",
      requestId: "rid-403",
    });
  });
});
