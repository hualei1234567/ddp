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

  it("创建任务只提交服务端允许字段并携带幂等键", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(collectionTaskFixture, 201),
    );
    const api = createCollectionsApi(clientWith(fetchImpl));

    const task = await api.createTask(
      PROJECT_ID,
      {
        code: "IT-307",
        name: "DDP-307 集成测试任务",
        priority: "P1",
        target_episode_count: 0,
        target_duration_sec: 0,
      },
      { idempotencyKey: "idem-create-307" },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(task.task_id).toBe(TASK_ID);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-tasks`,
    );
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "idem-create-307",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      code: "IT-307",
      name: "DDP-307 集成测试任务",
      priority: "P1",
      target_episode_count: 0,
      target_duration_sec: 0,
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
      `http://api.test/api/v1/projects/${PROJECT_ID}/episodes?limit=100&status=READY&task_id=${TASK_ID}`,
    );
  });

  it("Episode 列表把质量/标注/机器人/机位/捕获区间下发为服务端参数", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }));
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));
    const robotId = "33333333-3333-7333-8333-333333333333";

    await api.listEpisodes(PROJECT_ID, {
      qualityStatus: "PASS",
      annotationStatus: "APPROVED",
      robotId,
      camera: "main",
      capturedFrom: "2026-11-09T00:00:00.000Z",
      capturedTo: "2026-11-10T00:00:00.000Z",
    });

    const url = String(fetchImpl.mock.calls[0]?.[0]);
    const params = new URLSearchParams(url.split("?")[1] ?? "");
    expect(params.get("quality_status")).toBe("PASS");
    expect(params.get("annotation_status")).toBe("APPROVED");
    expect(params.get("robot_id")).toBe(robotId);
    expect(params.get("camera")).toBe("main");
    expect(params.get("captured_from")).toBe("2026-11-09T00:00:00.000Z");
    expect(params.get("captured_to")).toBe("2026-11-10T00:00:00.000Z");
  });

  it("Episode 详情、动作与修订使用真实路径、If-Match 与幂等键", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(episodeFixture))
      .mockResolvedValueOnce(jsonResponse(episodeFixture))
      .mockResolvedValueOnce(jsonResponse(episodeFixture, 201));
    const api = createCollectionsApi(clientWith(fetchImpl as typeof fetch));

    await api.getEpisode(PROJECT_ID, episodeFixture.episode_id);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/episodes/${episodeFixture.episode_id}`,
    );

    await api.actOnEpisode(
      PROJECT_ID,
      episodeFixture.episode_id,
      "quality-result",
      { quality_status: "PASS", reason: "抽检通过" },
      { version: 4, idempotencyKey: "idem-episode-action" },
    );
    const actionInit = fetchImpl.mock.calls[1]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/episodes/${episodeFixture.episode_id}/actions/quality-result`,
    );
    expect(actionInit?.headers).toMatchObject({
      "If-Match": '"4"',
      "Idempotency-Key": "idem-episode-action",
    });
    expect(JSON.parse(String(actionInit?.body))).toEqual({
      quality_status: "PASS",
      reason: "抽检通过",
    });

    await api.reviseEpisode(
      PROJECT_ID,
      episodeFixture.episode_id,
      { code: "EP-0001-R2" },
      { version: 5, idempotencyKey: "idem-episode-revision" },
    );
    const revisionInit = fetchImpl.mock.calls[2]?.[1] as
      | RequestInit
      | undefined;
    expect(fetchImpl.mock.calls[2]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/episodes/${episodeFixture.episode_id}/revisions`,
    );
    expect(revisionInit?.headers).toMatchObject({
      "If-Match": '"5"',
      "Idempotency-Key": "idem-episode-revision",
    });
    expect(JSON.parse(String(revisionInit?.body))).toEqual({
      code: "EP-0001-R2",
    });
  });

  it("创建批次只提交契约字段并携带幂等键", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(collectionBatchFixture, 201),
    );
    const api = createCollectionsApi(clientWith(fetchImpl));

    const batch = await api.createBatch(
      PROJECT_ID,
      TASK_ID,
      {
        expected_count: 8,
        code: "B-EXTRA-01",
        sequence_no: 2,
        assigned_to: null,
        scheduled_start: null,
        scheduled_end: null,
      },
      { idempotencyKey: "idem-batch-create" },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(batch.batch_id).toBe(collectionBatchFixture.batch_id);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-tasks/${TASK_ID}/batches`,
    );
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "idem-batch-create",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_count: 8,
      code: "B-EXTRA-01",
      sequence_no: 2,
      assigned_to: null,
      scheduled_start: null,
      scheduled_end: null,
    });
  });

  it("批次动作走 collection-batches 路径并带 If-Match 与原因", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(collectionBatchFixture),
    );
    const api = createCollectionsApi(clientWith(fetchImpl));

    await api.actOnBatch(
      PROJECT_ID,
      collectionBatchFixture.batch_id,
      "pause",
      {
        version: 3,
        reason: "设备故障",
        idempotencyKey: "idem-batch-action",
      },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${PROJECT_ID}/collection-batches/${collectionBatchFixture.batch_id}/actions/pause`,
    );
    expect(init?.headers).toMatchObject({
      "If-Match": '"3"',
      "Idempotency-Key": "idem-batch-action",
    });
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "设备故障" });
  });

  it("批次分配动作附带 assigned_to，未传时不发送该字段", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(collectionBatchFixture))
      .mockResolvedValueOnce(jsonResponse(collectionBatchFixture));
    const api = createCollectionsApi(clientWith(fetchImpl));

    await api.actOnBatch(PROJECT_ID, collectionBatchFixture.batch_id, "assign", {
      version: 3,
      assignedTo: "88888888-8888-7888-8888-888888888888",
    });
    expect(
      JSON.parse(String((fetchImpl.mock.calls[0]?.[1] as RequestInit).body)),
    ).toEqual({
      reason: null,
      assigned_to: "88888888-8888-7888-8888-888888888888",
    });

    await api.actOnBatch(PROJECT_ID, collectionBatchFixture.batch_id, "ready", {
      version: 3,
    });
    expect(
      JSON.parse(String((fetchImpl.mock.calls[1]?.[1] as RequestInit).body)),
    ).toEqual({ reason: null });
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
