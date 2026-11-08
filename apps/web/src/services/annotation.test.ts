import { describe, expect, it } from "vitest";

import {
  annotationReviewFixture,
  annotationSchemaFixture,
  annotationTaskFixture,
  goldSetFixture,
  iaaReportFixture,
  PROJECT_ID,
  TASK_ID,
} from "./__fixtures__/annotationFixtures";
import { ApiClient } from "./api";
import { createAnnotationApi } from "./annotation";

interface Call {
  url: string;
  init: RequestInit;
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(handler: (call: Call) => Response): {
  client: ApiClient;
  calls: Call[];
} {
  const calls: Call[] = [];
  const client = new ApiClient({
    baseUrl: "http://127.0.0.1:8000",
    fetchImpl: async (input, init) => {
      const call = { url: String(input), init: init ?? {} };
      calls.push(call);
      return handler(call);
    },
  });
  return { client, calls };
}

describe("标注 API 项目隔离与写请求头", () => {
  it("列表响应经严格解析并校验 project_id", async () => {
    const { client } = clientWith(() =>
      jsonResponse({
        items: [annotationSchemaFixture],
        next_cursor: null,
      }),
    );
    const page = await createAnnotationApi(client).listSchemas(PROJECT_ID);
    expect(page.items[0]?.schema_id).toBe(annotationSchemaFixture.schema_id);
  });

  it("响应 project_id 与路径不一致时拒绝进入 UI", async () => {
    const { client } = clientWith(() =>
      jsonResponse({
        items: [
          {
            ...annotationSchemaFixture,
            project_id: "99999999-9999-7999-8999-999999999999",
          },
        ],
        next_cursor: null,
      }),
    );
    await expect(
      createAnnotationApi(client).listSchemas(PROJECT_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("未知枚举经解析器拒绝，而不是透传为字符串", async () => {
    const { client } = clientWith(() =>
      jsonResponse({
        items: [{ ...annotationSchemaFixture, status: "NOT_A_STATUS" }],
        next_cursor: null,
      }),
    );
    await expect(
      createAnnotationApi(client).listSchemas(PROJECT_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("创建任务携带 Idempotency-Key 与请求体", async () => {
    const { client, calls } = clientWith(() =>
      jsonResponse(annotationTaskFixture, 201),
    );
    const task = await createAnnotationApi(client).createTask(PROJECT_ID, {
      code: "AN-0001",
      name: "任务",
      sample_set_id: annotationTaskFixture.sample_set_id,
      schema_id: annotationTaskFixture.schema_id,
      target_item_count: 1,
      target_episode_count: 1,
    });
    expect(task.task_id).toBe(annotationTaskFixture.task_id);
    const call = calls[0];
    expect(call?.url).toContain(
      `/api/v1/projects/${PROJECT_ID}/annotation-tasks`,
    );
    const headers = new Headers(call?.init.headers);
    expect(headers.get("Idempotency-Key")?.length).toBeGreaterThanOrEqual(8);
    const body = JSON.parse(String(call?.init.body)) as Record<string, unknown>;
    expect(body.code).toBe("AN-0001");
    expect(body.target_item_count).toBe(1);
  });

  it("复核动作携带 If-Match 与问题项/检查项", async () => {
    const { client, calls } = clientWith(() =>
      jsonResponse({ ...annotationReviewFixture, version: 3 }),
    );
    await createAnnotationApi(client).actOnReview(
      PROJECT_ID,
      annotationReviewFixture.review_id,
      "reject",
      {
        version: 2,
        reason: "标签错误",
        issues: [
          {
            code: "LABEL_VALUE_INVALID",
            severity: "MAJOR",
            message: "动作与证据不一致",
            label_key: "action",
            item_ref: null,
            evidence_ref: null,
          },
        ],
      },
    );
    const headers = new Headers(calls[0]?.init.headers);
    expect(headers.get("If-Match")).toBe('"2"');
    expect(headers.get("Idempotency-Key")?.length).toBeGreaterThanOrEqual(8);
    const body = JSON.parse(String(calls[0]?.init.body)) as Record<string, unknown>;
    expect(body.reason).toBe("标签错误");
    expect(Array.isArray(body.issues)).toBe(true);
  });

  it("IAA 列表按 task_id 查询并保留 null 指标值", async () => {
    const { client, calls } = clientWith(() =>
      jsonResponse({ items: [iaaReportFixture], next_cursor: null }),
    );
    const page = await createAnnotationApi(client).listIAAReports(
      PROJECT_ID,
      TASK_ID,
    );
    expect(calls[0]?.url).toContain(`task_id=${TASK_ID}`);
    expect(page.items[0]?.metrics[0]?.value).toBeNull();
  });

  it("黄金集答案路径独立，响应仍校验 project_id", async () => {
    const { client, calls } = clientWith(() =>
      jsonResponse({
        ...goldSetFixture,
        project_id: "99999999-9999-7999-8999-999999999999",
      }),
    );
    await expect(
      createAnnotationApi(client).getGoldSetAnswers(
        PROJECT_ID,
        "ffffffff-aaaa-7bbb-8ccc-dddddddddddd",
      ),
    ).rejects.toMatchObject({ kind: "invalid_response" });
    expect(calls[0]?.url).toContain(
      `/gold-sets/ffffffff-aaaa-7bbb-8ccc-dddddddddddd/answers`,
    );
  });
});
