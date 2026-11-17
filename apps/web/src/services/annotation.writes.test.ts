/**
 * 标注链路新增写接口的请求层测试。
 *
 * 与 `annotation.requests.test.ts` 同样的策略：每个方法断言完整路径、
 * HTTP 方法、If-Match / Idempotency-Key 与请求体关键字段。
 * 响应解析失败不影响请求断言——请求在解析前已经发出。
 */
import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "./api";
import { createAnnotationApi } from "./annotation";

const PROJECT = "11111111-1111-7111-8111-111111111111";
const ID = "22222222-2222-7222-8222-222222222222";
const OTHER_ID = "33333333-3333-7333-8333-333333333333";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

type Call = [string, RequestInit];

function makeApi(body: unknown = {}) {
  const fetchImpl = vi.fn(async () => jsonResponse(body));
  const apiClient = new ApiClient({
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { api: createAnnotationApi(apiClient), fetchImpl };
}

function lastCall(fetchImpl: ReturnType<typeof vi.fn>): Call {
  return fetchImpl.mock.calls.at(-1) as unknown as Call;
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

function bodyOf(init: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init.body));
}

const writeCases: Array<{
  name: string;
  method: "POST" | "PATCH";
  path: string;
  call: (api: ReturnType<typeof createAnnotationApi>) => Promise<unknown>;
  body?: Record<string, unknown>;
  noMatch?: boolean;
}> = [
  {
    name: "createSchema",
    method: "POST",
    path: `/annotation-schemas`,
    noMatch: true,
    call: (api) =>
      api.createSchema(PROJECT, {
        code: "kitchen",
        name: "厨房",
        annotation_levels: ["EPISODE"],
        labels: [],
      }),
    body: { code: "kitchen", name: "厨房" },
  },
  {
    name: "updateSchema",
    method: "PATCH",
    path: `/annotation-schemas/${ID}`,
    call: (api) =>
      api.updateSchema(
        PROJECT,
        ID,
        { name: "新名称" },
        { version: 4 },
      ),
    body: { name: "新名称" },
  },
  {
    name: "actOnSchema",
    method: "POST",
    path: `/annotation-schemas/${ID}/actions/publish`,
    call: (api) =>
      api.actOnSchema(PROJECT, ID, "publish", { version: 2, reason: "审核通过" }),
    body: { reason: "审核通过" },
  },
  {
    name: "createSampleSet",
    method: "POST",
    path: `/annotation-sample-sets`,
    noMatch: true,
    call: (api) =>
      api.createSampleSet(PROJECT, {
        code: "ss-1",
        filters: { include_episode_ids: [OTHER_ID] },
        data_source: "REAL",
      }),
    body: {
      code: "ss-1",
      filters: { include_episode_ids: [OTHER_ID] },
      data_source: "REAL",
    },
  },
  {
    name: "actOnSampleSet",
    method: "POST",
    path: `/annotation-sample-sets/${ID}/actions/cancel`,
    call: (api) =>
      api.actOnSampleSet(PROJECT, ID, "cancel", {
        version: 3,
        reason: "选择规则有误",
      }),
    body: { reason: "选择规则有误" },
  },
  {
    name: "createBatch",
    method: "POST",
    path: `/annotation-tasks/${ID}/batches`,
    noMatch: true,
    call: (api) =>
      api.createBatch(PROJECT, ID, {
        planned_item_count: 50,
        name: "第一批",
      }),
    body: { planned_item_count: 50, name: "第一批" },
  },
  {
    name: "actOnBatch",
    method: "POST",
    path: `/annotation-batches/${ID}/actions/ready`,
    call: (api) =>
      api.actOnBatch(PROJECT, ID, "ready", { version: 1 }),
    body: { reason: null },
  },
  {
    name: "createAssignment",
    method: "POST",
    path: `/annotation-assignments`,
    noMatch: true,
    call: (api) =>
      api.createAssignment(PROJECT, {
        batch_id: ID,
        assignee_id: OTHER_ID,
        assignment_role: "ANNOTATOR",
        planned_item_count: 10,
      }),
    body: { batch_id: ID, assignee_id: OTHER_ID },
  },
  {
    name: "actOnAssignment",
    method: "POST",
    path: `/annotation-assignments/${ID}/actions/submit`,
    call: (api) =>
      api.actOnAssignment(PROJECT, ID, "submit", {
        version: 2,
        submittedItemCount: 8,
      }),
    body: { submitted_item_count: 8 },
  },
  {
    name: "planQualitySamples",
    method: "POST",
    path: `/quality-samples/plan`,
    noMatch: true,
    call: (api) =>
      api.planQualitySamples(PROJECT, {
        task_id: ID,
        candidates: [{ submission_id: OTHER_ID }],
        sampling_policy_version: "policy/1",
        sample_type: "RANDOM",
        sample_size: 1,
        sample_reason: "例行抽检",
      }),
    body: {
      task_id: ID,
      sampling_policy_version: "policy/1",
      sample_size: 1,
    },
  },
  {
    name: "createQualitySample",
    method: "POST",
    path: `/quality-samples`,
    noMatch: true,
    call: (api) =>
      api.createQualitySample(PROJECT, {
        task_id: ID,
        submission_id: OTHER_ID,
        annotator_id: ID,
        sampling_policy_version: "policy/1",
        sample_type: "RANDOM",
        sample_reason: "手工抽检",
      }),
    body: { task_id: ID, submission_id: OTHER_ID, sample_reason: "手工抽检" },
  },
  {
    name: "actOnQualitySample",
    method: "POST",
    path: `/quality-samples/${ID}/actions/assign`,
    call: (api) =>
      api.actOnQualitySample(PROJECT, ID, "assign", {
        version: 1,
        reviewerId: OTHER_ID,
      }),
    body: { reviewer_id: OTHER_ID },
  },
  {
    name: "evaluateGoldSet",
    method: "POST",
    path: `/gold-sets/${ID}/evaluations`,
    noMatch: true,
    call: (api) =>
      api.evaluateGoldSet(PROJECT, ID, {
        task_id: ID,
        submission_id: OTHER_ID,
      }),
    body: { task_id: ID, submission_id: OTHER_ID },
  },
  {
    name: "injectGoldSet",
    method: "POST",
    path: `/gold-sets/${ID}/injections`,
    noMatch: true,
    call: (api) =>
      api.injectGoldSet(PROJECT, ID, {
        task_id: ID,
        annotator_id: OTHER_ID,
        item_ids: [ID],
      }),
    body: { task_id: ID, annotator_id: OTHER_ID, item_ids: [ID] },
  },
  {
    name: "confirmAiSuggestion",
    method: "POST",
    path: `/annotation-ai-suggestions/${ID}/confirm`,
    call: (api) =>
      api.confirmAiSuggestion(PROJECT, ID, { action: "pick" }, { version: 3 }),
    body: { human_value: { action: "pick" } },
  },
];

describe("标注链路写接口请求契约", () => {
  for (const item of writeCases) {
    it(`${item.name} 使用 ${item.method} 并携带正确路径与请求体`, async () => {
      const { api, fetchImpl } = makeApi();
      await expect(item.call(api)).rejects.toBeTruthy();

      const [url, init] = lastCall(fetchImpl);
      expect(url).toContain(`/api/v1/projects/${PROJECT}${item.path}`);
      expect(init.method).toBe(item.method);
      const headers = headersOf(init);
      expect(headers["Idempotency-Key"]).toBeTruthy();
      if (item.noMatch) {
        expect(headers["If-Match"]).toBeUndefined();
      } else {
        expect(headers["If-Match"]).toMatch(/^"\d+"$/);
      }
      for (const [key, value] of Object.entries(item.body ?? {})) {
        expect(bodyOf(init)[key]).toEqual(value);
      }
    });
  }

  it("listAiSuggestions 路径正确且为 GET", async () => {
    const { api, fetchImpl } = makeApi({ items: [], next_cursor: null });
    const page = await api.listAiSuggestions(PROJECT, ID);
    expect(page.items).toEqual([]);
    const [url, init] = lastCall(fetchImpl);
    expect(url).toContain(
      `/api/v1/projects/${PROJECT}/annotation-submissions/${ID}/ai-suggestions`,
    );
    expect(init.method ?? "GET").toBe("GET");
  });

  it("getSuggestionAgreement 解析 items 与 null 采纳率", async () => {
    const { api, fetchImpl } = makeApi({
      items: [
        {
          model_id: "m1",
          model_version: "v1",
          total: 3,
          accepted: 0,
          revised: 0,
          pending: 3,
          decided: 0,
          acceptance_rate: null,
        },
      ],
    });
    const agreement = await api.getSuggestionAgreement(PROJECT, ID);
    expect(agreement.items[0]?.acceptance_rate).toBeNull();
    const [url] = lastCall(fetchImpl);
    expect(url).toContain(
      `/annotation-submissions/${ID}/ai-suggestions/agreement`,
    );
  });

  it("planQualitySamples 要求服务端返回数组", async () => {
    const { api } = makeApi({ items: [] });
    await expect(
      api.planQualitySamples(PROJECT, {
        task_id: ID,
        candidates: [{ submission_id: OTHER_ID }],
        sampling_policy_version: "policy/1",
        sample_type: "RANDOM",
        sample_size: 1,
        sample_reason: "例行抽检",
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });
});
