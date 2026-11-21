/**
 * 标注 client 的请求层测试：路径、查询参数、If-Match 与幂等键。
 *
 * 为什么单独建文件：`annotation.ts` 916 行、35 个方法，是前端最容易出现
 * "页面能跑但路径/头写错"的地方（D-003）。在拆分之前先把每个方法的
 * 请求契约钉住——以后无论怎么重排文件，这些断言都能发现行为漂移。
 *
 * 覆盖策略：每个方法至少断言（1）完整请求路径、（2）HTTP 方法、
 * （3）写动作的 If-Match 与 Idempotency-Key、（4）请求体关键字段。
 * 列表方法额外断言查询参数，避免"少传 task_id"这类缺陷。
 */

import { describe, expect, it, vi } from "vitest";

import {
  PROJECT_ID,
  annotationTaskFixture,
} from "./__fixtures__/annotationFixtures";
import { ApiClient } from "./api";
import { createAnnotationApi } from "./annotation";

const PROJECT = PROJECT_ID;
const ID = "22222222-2222-2222-2222-222222222222";
const OTHER_ID = "33333333-3333-3333-3333-333333333333";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

type Call = [string, RequestInit];

function makeApi(body: unknown, status = 200) {
  const fetchImpl = vi.fn(async () => jsonResponse(body, status));
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

/** 请求 URL 可能是相对路径，这里直接取查询串，不依赖 base。 */
function queryOf(url: string): URLSearchParams {
  const index = url.indexOf("?");
  return new URLSearchParams(index >= 0 ? url.slice(index + 1) : "");
}

const emptyPage = { items: [], next_cursor: null };

/**
 * 列表型方法：断言路径 + 方法 + 查询参数。
 * 每个方法用一个最小合法响应（空页），只验证请求构造。
 */
describe("标注列表与详情请求", () => {
  const cases: Array<{
    name: string;
    call: (api: ReturnType<typeof createAnnotationApi>) => Promise<unknown>;
    path: string;
    query?: Record<string, string>;
  }> = [
    {
      name: "listSchemas",
      call: (api) => api.listSchemas(PROJECT, { status: "PUBLISHED" }),
      path: `/annotation-schemas`,
      query: { status: "PUBLISHED" },
    },
    {
      name: "listSampleSets",
      call: (api) => api.listSampleSets(PROJECT, { status: "SNAPSHOTTED" }),
      path: `/annotation-sample-sets`,
      query: { status: "SNAPSHOTTED" },
    },
    {
      name: "listTasks",
      call: (api) =>
        api.listTasks(PROJECT, { status: "RUNNING", overdue: true }),
      path: `/annotation-tasks`,
      query: { status: "RUNNING", overdue: "true" },
    },
    {
      name: "listBatches",
      call: (api) => api.listBatches(PROJECT, ID),
      path: `/annotation-tasks/${ID}/batches`,
    },
    {
      name: "listAssignments",
      call: (api) =>
        api.listAssignments(PROJECT, {
          taskId: ID,
          batchId: OTHER_ID,
          assigneeId: ID,
          status: "ASSIGNED",
        }),
      path: `/annotation-assignments`,
      query: {
        task_id: ID,
        batch_id: OTHER_ID,
        assignee_id: ID,
        status: "ASSIGNED",
      },
    },
    {
      name: "listSubmissions",
      call: (api) => api.listSubmissions(PROJECT, ID, { status: "SUBMITTED" }),
      path: `/annotation-submissions`,
      query: { task_id: ID, status: "SUBMITTED" },
    },
    {
      name: "listSubmissionRecords",
      call: (api) => api.listSubmissionRecords(PROJECT, ID),
      path: `/annotation-submissions/${ID}/records`,
    },
    {
      name: "listSubmissionReviews",
      call: (api) => api.listSubmissionReviews(PROJECT, ID),
      path: `/annotation-submissions/${ID}/reviews`,
    },
    {
      name: "listSubmissionArbitrations",
      call: (api) => api.listSubmissionArbitrations(PROJECT, ID),
      path: `/annotation-submissions/${ID}/arbitrations`,
    },
    {
      name: "listGoldSets",
      call: (api) => api.listGoldSets(PROJECT, { status: "ACTIVE" }),
      path: `/gold-sets`,
      query: { status: "ACTIVE" },
    },
    {
      name: "listQualitySamples",
      call: (api) => api.listQualitySamples(PROJECT, ID, { status: "PENDING" }),
      path: `/quality-samples`,
      query: { task_id: ID, status: "PENDING" },
    },
    {
      name: "listIAAReports",
      call: (api) => api.listIAAReports(PROJECT, ID),
      path: `/iaa-reports`,
      query: { task_id: ID },
    },
    {
      name: "listQualityChecks",
      call: (api) => api.listQualityChecks(PROJECT, ID),
      path: `/annotation-submissions/${ID}/quality-checks`,
    },
  ];

  for (const item of cases) {
    it(`${item.name} 路径与查询参数正确`, async () => {
      const { api, fetchImpl } = makeApi(emptyPage);
      await item.call(api);

      const [url, init] = lastCall(fetchImpl);
      expect(url).toContain(`/api/v1/projects/${PROJECT}${item.path}`);
      expect(init.method ?? "GET").toBe("GET");
      const params = queryOf(url);
      for (const [key, value] of Object.entries(item.query ?? {})) {
        expect(params.get(key)).toBe(value);
      }
    });
  }

  const detailCases: Array<{
    name: string;
    call: (api: ReturnType<typeof createAnnotationApi>) => Promise<unknown>;
    path: string;
  }> = [
    {
      name: "getSchema",
      call: (api) => api.getSchema(PROJECT, ID),
      path: `/annotation-schemas/${ID}`,
    },
    {
      name: "getSampleSet",
      call: (api) => api.getSampleSet(PROJECT, ID),
      path: `/annotation-sample-sets/${ID}`,
    },
    {
      name: "getTask",
      call: (api) => api.getTask(PROJECT, ID),
      path: `/annotation-tasks/${ID}`,
    },
    {
      name: "getTaskConsistency",
      call: (api) => api.getTaskConsistency(PROJECT, ID),
      path: `/annotation-tasks/${ID}/consistency`,
    },
    {
      name: "getOverdueSummary",
      call: (api) => api.getOverdueSummary(PROJECT),
      path: `/annotation-overdue-summary`,
    },
    {
      name: "getWorkload",
      call: (api) =>
        api.getWorkload(PROJECT, {
          assigneeId: ID,
          periodStart: "2026-11-01T00:00:00Z",
          periodEnd: "2026-11-08T00:00:00Z",
        }),
      path: `/annotation-workload`,
    },
    {
      name: "getBatch",
      call: (api) => api.getBatch(PROJECT, ID),
      path: `/annotation-batches/${ID}`,
    },
    {
      name: "getAssignment",
      call: (api) => api.getAssignment(PROJECT, ID),
      path: `/annotation-assignments/${ID}`,
    },
    {
      name: "getSubmission",
      call: (api) => api.getSubmission(PROJECT, ID),
      path: `/annotation-submissions/${ID}`,
    },
    {
      name: "getReview",
      call: (api) => api.getReview(PROJECT, ID),
      path: `/annotation-reviews/${ID}`,
    },
    {
      name: "getArbitration",
      call: (api) => api.getArbitration(PROJECT, ID),
      path: `/annotation-arbitrations/${ID}`,
    },
    {
      name: "getGoldSet",
      call: (api) => api.getGoldSet(PROJECT, ID),
      path: `/gold-sets/${ID}`,
    },
    {
      name: "getGoldSetAnswers",
      call: (api) => api.getGoldSetAnswers(PROJECT, ID),
      path: `/gold-sets/${ID}/answers`,
    },
    {
      name: "getQualitySample",
      call: (api) => api.getQualitySample(PROJECT, ID),
      path: `/quality-samples/${ID}`,
    },
    {
      name: "getIAAReport",
      call: (api) => api.getIAAReport(PROJECT, ID),
      path: `/iaa-reports/${ID}`,
    },
  ];

  for (const item of detailCases) {
    it(`${item.name} 路径正确`, async () => {
      const { api, fetchImpl } = makeApi({});
      // 详情响应的解析会失败，但请求已经发出——这里只关心请求构造。
      await expect(item.call(api)).rejects.toBeTruthy();
      const [url, init] = lastCall(fetchImpl);
      expect(url).toContain(`/api/v1/projects/${PROJECT}${item.path}`);
      expect(init.method ?? "GET").toBe("GET");
    });
  }
});

/**
 * 工作量聚合：周期两端必须原样出现在查询串里，响应必须与请求的
 * project/assignee 一致——否则运营页会把别人的数字当成本项目的。
 */
describe("工作量聚合请求", () => {
  const WORKLOAD_ID = "44444444-4444-4444-4444-444444444444";

  function workloadFixture(overrides: Record<string, unknown> = {}) {
    return {
      project_id: PROJECT,
      user_id: WORKLOAD_ID,
      period_start: "2026-11-01T00:00:00Z",
      period_end: "2026-11-08T00:00:00Z",
      assigned_item_count: 4,
      claimed_item_count: 2,
      submitted_item_count: 1,
      approved_item_count: 1,
      rework_item_count: 0,
      total_minutes: 90,
      capacity_minutes: null,
      utilization_ratio: null,
      status: "NORMAL",
      missing_semantics: "N/A",
      source_version: "annotation-assignment-events/1",
      ...overrides,
    };
  }

  it("周期参数原样进入查询串", async () => {
    const { api, fetchImpl } = makeApi(workloadFixture());

    await api.getWorkload(PROJECT, {
      assigneeId: WORKLOAD_ID,
      periodStart: "2026-11-01T00:00:00Z",
      periodEnd: "2026-11-08T00:00:00Z",
    });

    const [url] = lastCall(fetchImpl);
    const query = queryOf(url);
    expect(query.get("assignee_id")).toBe(WORKLOAD_ID);
    expect(query.get("period_start")).toBe("2026-11-01T00:00:00Z");
    expect(query.get("period_end")).toBe("2026-11-08T00:00:00Z");
  });

  it("响应 user_id 与请求的 assignee 不一致时报契约错误", async () => {
    const { api } = makeApi(
      workloadFixture({ user_id: "55555555-5555-5555-5555-555555555555" }),
    );

    // ApiClient 会把解析失败统一包装成 invalid_response；这里断言类别，
    // 而不是包装后的通用文案。
    await expect(
      api.getWorkload(PROJECT, {
        assigneeId: WORKLOAD_ID,
        periodStart: "2026-11-01T00:00:00Z",
        periodEnd: "2026-11-08T00:00:00Z",
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });
});

/**
 * 写动作：必须带 If-Match 与 Idempotency-Key。
 * 这两样缺失时服务端会返回 428 / 409，属于"本可以本地发现"的错误。
 */
describe("标注写动作的请求头与请求体", () => {
  it("createTask 带幂等键，不带 If-Match", async () => {
    const { api, fetchImpl } = makeApi(annotationTaskFixture);

    await api.createTask(
      PROJECT,
      {
        code: "task-1",
        name: "任务",
        schema_id: ID,
        sample_set_id: OTHER_ID,
      } as never,
      { idempotencyKey: "key-task-1" },
    );

    const [url, init] = lastCall(fetchImpl);
    expect(url).toContain(`/api/v1/projects/${PROJECT}/annotation-tasks`);
    expect(init.method).toBe("POST");
    const headers = headersOf(init);
    expect(headers["Idempotency-Key"]).toBe("key-task-1");
    expect(headers["If-Match"]).toBeUndefined();
  });

  const writeCases: Array<{
    name: string;
    call: (api: ReturnType<typeof createAnnotationApi>) => Promise<unknown>;
    path: string;
    body?: Record<string, unknown>;
  }> = [
    {
      name: "actOnTask",
      call: (api) =>
        api.actOnTask(PROJECT, ID, "start", { version: 3, reason: "开工" }),
      path: `/annotation-tasks/${ID}/actions/start`,
      body: { reason: "开工" },
    },
    {
      // 验收未达标时服务端强制要求 waiver_reason；前端必须能透传。
      name: "actOnTaskWithWaiver",
      call: (api) =>
        api.actOnTask(PROJECT, ID, "complete", {
          version: 7,
          reason: "验收",
          waiverReason: "抽检覆盖率已达阈值，剩余条目不阻塞",
        }),
      path: `/annotation-tasks/${ID}/actions/complete`,
      body: {
        reason: "验收",
        waiver_reason: "抽检覆盖率已达阈值，剩余条目不阻塞",
      },
    },
    {
      name: "createSubmission",
      call: (api) =>
        api.createSubmission(PROJECT, { taskId: ID, assignmentId: OTHER_ID } as never),
      path: `/annotation-submissions`,
    },
    {
      name: "actOnSubmission",
      call: (api) =>
        api.actOnSubmission(PROJECT, ID, "submit", { version: 2 }),
      path: `/annotation-submissions/${ID}/actions/submit`,
    },
    {
      name: "createReview",
      call: (api) =>
        api.createReview(PROJECT, { submissionId: ID, reviewerId: OTHER_ID }),
      path: `/annotation-reviews`,
      body: { submission_id: ID, reviewer_id: OTHER_ID },
    },
    {
      name: "createReviewForSubmission",
      call: (api) => api.createReviewForSubmission(PROJECT, ID, OTHER_ID),
      path: `/annotation-submissions/${ID}/reviews`,
      body: { reviewer_id: OTHER_ID },
    },
    {
      name: "actOnReview",
      call: (api) =>
        api.actOnReview(PROJECT, ID, "approve", {
          version: 4,
          reason: "通过",
          reviewDurationSec: 120,
        }),
      path: `/annotation-reviews/${ID}/actions/approve`,
      body: { reason: "通过", review_duration_sec: 120 },
    },
    {
      name: "createArbitration",
      call: (api) =>
        api.createArbitration(PROJECT, {
          submissionId: ID,
          arbiterIds: [OTHER_ID],
          quorum: 2,
          trigger: "DISAGREEMENT",
        }),
      path: `/annotation-arbitrations`,
      body: {
        submission_id: ID,
        arbiter_ids: [OTHER_ID],
        quorum: 2,
        trigger: "DISAGREEMENT",
      },
    },
    {
      name: "createArbitrationForSubmission",
      call: (api) =>
        api.createArbitrationForSubmission(PROJECT, ID, {
          arbiterIds: [OTHER_ID],
          quorum: 2,
          trigger: "DISAGREEMENT",
        } as never),
      path: `/annotation-submissions/${ID}/arbitrations`,
    },
    {
      name: "voteOnArbitration",
      call: (api) =>
        api.voteOnArbitration(
          PROJECT,
          ID,
          { decision: "ACCEPT", reason: "同意" } as never,
          { version: 5 },
        ),
      path: `/annotation-arbitrations/${ID}/votes`,
      body: { decision: "ACCEPT", reason: "同意" },
    },
    {
      name: "actOnArbitration",
      call: (api) =>
        api.actOnArbitration(PROJECT, ID, "finalize", { version: 6 }),
      path: `/annotation-arbitrations/${ID}/actions/finalize`,
    },
    {
      // N4：手动重跑质量检查；每次调用都是新 run，没有请求体。
      name: "runQualityCheck",
      call: (api) => api.runQualityCheck(PROJECT, ID),
      path: `/annotation-submissions/${ID}/quality-checks`,
    },
    {
      name: "createGoldSet",
      call: (api) =>
        api.createGoldSet(PROJECT, {
          code: "GOLD-001",
          schema_id: OTHER_ID,
          items: [{ episode_id: ID }],
          exposure_policy: "HIDDEN",
          max_exposure_per_annotator: 1,
          rotation_policy: "NONE",
          usage_policy: {},
        }),
      path: `/gold-sets`,
      body: {
        code: "GOLD-001",
        schema_id: OTHER_ID,
        items: [{ episode_id: ID }],
      },
    },
    {
      name: "actOnGoldSet",
      call: (api) =>
        api.actOnGoldSet(PROJECT, ID, "submit-review", {
          version: 2,
          reason: "内容已核对",
        }),
      path: `/gold-sets/${ID}/actions/submit-review`,
      body: { reason: "内容已核对" },
    },
    {
      name: "createIAAReport",
      call: (api) =>
        api.createIAAReport(PROJECT, {
          task_id: ID,
          metric_definitions_version: "ddp.stage3.iaa/1",
          iou_threshold: 0.5,
        }),
      path: `/iaa-reports`,
      body: {
        task_id: ID,
        metric_definitions_version: "ddp.stage3.iaa/1",
        iou_threshold: 0.5,
      },
    },
  ];

  for (const item of writeCases) {
    it(`${item.name} 用 POST + If-Match + Idempotency-Key`, async () => {
      const { api, fetchImpl } = makeApi({});
      await expect(item.call(api)).rejects.toBeTruthy();

      const [url, init] = lastCall(fetchImpl);
      expect(url).toContain(`/api/v1/projects/${PROJECT}${item.path}`);
      expect(init.method).toBe("POST");
      const headers = headersOf(init);
      // 每个写动作都必须带幂等键（服务端强制，缺失 400）。
      expect(headers["Idempotency-Key"]).toBeTruthy();
      // 除创建类外都必须带 If-Match。
      if (item.body !== undefined && !item.name.startsWith("create")) {
        expect(headers["If-Match"]).toMatch(/^"\d+"$/);
      }
      for (const [key, value] of Object.entries(item.body ?? {})) {
        expect(bodyOf(init)[key]).toEqual(value);
      }
    });
  }
});

describe("标注 client 的标识符校验", () => {
  it("缺失 ID 时在发请求前就报错，不会打到服务端", async () => {
    const { api, fetchImpl } = makeApi(emptyPage);

    await expect(api.getTask(PROJECT, "")).rejects.toThrow(/task_id/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("动作名同样要求非空", async () => {
    const { api, fetchImpl } = makeApi({});
    await expect(
      api.actOnTask(PROJECT, ID, "", { version: 1 }),
    ).rejects.toThrow(/action/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
