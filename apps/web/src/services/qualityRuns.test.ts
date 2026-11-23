import { describe, expect, it, vi } from "vitest";

import {
  QUALITY_BASELINE_RUN_ID,
  QUALITY_CANDIDATE_RUN_ID,
  QUALITY_EPISODE_ID,
  QUALITY_IMPORT_ID,
  QUALITY_PROJECT_ID,
  QUALITY_RUN_ID,
  qualityRunDiffFixture,
  qualityRunFindingFixture,
  qualityRunFixture,
  queuedQualityRunFixture,
} from "./__fixtures__/qualityRunFixtures";

const QUALITY_OTHER_EPISODE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
import { ApiClient } from "./api";
import { createQualityRunApi } from "./qualityRuns";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function makeApi(handler: (url: string, init: RequestInit) => Response) {
  const fetchImpl = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) =>
      handler(String(input), init ?? {}),
  );
  const client = new ApiClient({
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { api: createQualityRunApi(client), fetchImpl };
}

describe("createQualityRunApi", () => {
  it("触发导入质检使用 POST，并对响应做项目与对象校验", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse(qualityRunFixture(), 201),
    );

    const run = await api.createRun(QUALITY_PROJECT_ID, {
      subject_type: "DATASOURCE_IMPORT",
      subject_id: QUALITY_IMPORT_ID,
    });

    expect(run.run_id).toBe(QUALITY_RUN_ID);
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs`,
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      subject_type: "DATASOURCE_IMPORT",
      subject_id: QUALITY_IMPORT_ID,
    });
  });

  it("响应对象与请求不一致时拒绝（防止串项目/串对象展示）", async () => {
    const { api } = makeApi(() =>
      jsonResponse(
        qualityRunFixture({
          subject_id: "99999999-9999-4999-8999-999999999999",
        }),
        201,
      ),
    );

    await expect(
      api.createRun(QUALITY_PROJECT_ID, {
        subject_type: "DATASOURCE_IMPORT",
        subject_id: QUALITY_IMPORT_ID,
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("按运行 ID 读取详情", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse(qualityRunFixture()),
    );

    await api.getRun(QUALITY_PROJECT_ID, QUALITY_RUN_ID);

    const [url] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs/${QUALITY_RUN_ID}`,
    );
  });

  it("结论明细带上类别/状态/游标/上限查询参数", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse({
        items: [qualityRunFindingFixture()],
        next_cursor: null,
      }),
    );

    await api.listFindings(QUALITY_PROJECT_ID, QUALITY_RUN_ID, {
      category: "INTEGRITY",
      status: "FAIL",
      cursor: "cursor-1",
      limit: 20,
    });

    const [url] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toContain(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs/${QUALITY_RUN_ID}/findings`,
    );
    expect(url).toContain("category=INTEGRITY");
    expect(url).toContain("status=FAIL");
    expect(url).toContain("cursor=cursor-1");
    expect(url).toContain("limit=20");
  });

  it("结论明细的 run_id 与请求不一致时拒绝", async () => {
    const { api } = makeApi(() =>
      jsonResponse({
        items: [
          qualityRunFindingFixture({
            run_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          }),
        ],
        next_cursor: null,
      }),
    );

    await expect(
      api.listFindings(QUALITY_PROJECT_ID, QUALITY_RUN_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("按对象列举历史运行：GET 带过滤/游标/上限并校验项目与对象", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse({
        items: [qualityRunFixture()],
        next_cursor: "cursor-2",
      }),
    );

    const page = await api.listRuns(QUALITY_PROJECT_ID, {
      subject_type: "DATASOURCE_IMPORT",
      subject_id: QUALITY_IMPORT_ID,
      cursor: "cursor-1",
      limit: 20,
    });

    expect(page.next_cursor).toBe("cursor-2");
    expect(page.items).toHaveLength(1);
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toContain(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs?`,
    );
    expect(url).toContain("subject_type=DATASOURCE_IMPORT");
    expect(url).toContain(`subject_id=${QUALITY_IMPORT_ID}`);
    expect(url).toContain("cursor=cursor-1");
    expect(url).toContain("limit=20");
    expect(init?.method ?? "GET").toBe("GET");
  });

  it("空结果原样返回，不被改写成报错或假数据", async () => {
    const { api } = makeApi(() =>
      jsonResponse({ items: [], next_cursor: null }),
    );

    const page = await api.listRuns(QUALITY_PROJECT_ID, {
      subject_type: "DATASOURCE_IMPORT",
      subject_id: QUALITY_IMPORT_ID,
    });

    expect(page).toEqual({ items: [], next_cursor: null });
  });

  it("列举结果串项目时拒绝整页", async () => {
    const { api } = makeApi(() =>
      jsonResponse({
        items: [
          qualityRunFixture({
            project_id: "99999999-9999-4999-8999-999999999999",
          }),
        ],
        next_cursor: null,
      }),
    );

    await expect(
      api.listRuns(QUALITY_PROJECT_ID, {
        subject_type: "DATASOURCE_IMPORT",
        subject_id: QUALITY_IMPORT_ID,
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("列举结果与请求的检查对象不一致时拒绝", async () => {
    const { api } = makeApi(() =>
      jsonResponse({
        items: [qualityRunFixture()],
        next_cursor: null,
      }),
    );

    await expect(
      api.listRuns(QUALITY_PROJECT_ID, {
        subject_type: "DATASOURCE_IMPORT",
        subject_id: "66666666-6666-4666-8666-666666666666",
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("空 subject_id 在发请求前拒绝", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse({ items: [], next_cursor: null }),
    );

    await expect(
      api.listRuns(QUALITY_PROJECT_ID, {
        subject_type: "DATASOURCE_IMPORT",
        subject_id: "   ",
      }),
    ).rejects.toThrow("subject_id 不能为空");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("批量重跑与差异报告 API（V31-033 / FR-026..028）", () => {
  it("批量重跑使用 POST /quality-runs/batch，请求体只含 EPISODE 与去重后的对象列表", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse([queuedQualityRunFixture()], 201),
    );

    const queued = await api.batchCreateRuns(QUALITY_PROJECT_ID, {
      subject_type: "EPISODE",
      subject_ids: [QUALITY_EPISODE_ID, QUALITY_EPISODE_ID, QUALITY_OTHER_EPISODE_ID],
    });

    expect(queued).toHaveLength(1);
    expect(queued[0]?.lifecycle).toBe("QUEUED");
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs/batch`,
    );
    expect(init?.method).toBe("POST");
    // 去重在前端完成：重复对象在服务端是 422，提前拦截避免整批被拒。
    expect(JSON.parse(String(init?.body))).toEqual({
      subject_type: "EPISODE",
      subject_ids: [QUALITY_EPISODE_ID, QUALITY_OTHER_EPISODE_ID],
    });
  });

  it("批量重跑响应必须是非空数组；入队 0 条是调用方错误", async () => {
    const { api } = makeApi(() => jsonResponse([], 201));

    await expect(
      api.batchCreateRuns(QUALITY_PROJECT_ID, {
        subject_type: "EPISODE",
        subject_ids: [QUALITY_EPISODE_ID],
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("排队回执逐条校验：缺结论字段是预期事实，串项目条目拒绝整批", async () => {
    const { api } = makeApi(() =>
      jsonResponse([
        queuedQualityRunFixture(),
        queuedQualityRunFixture({
          project_id: "99999999-9999-4999-8999-999999999999",
        }),
      ]),
    );

    await expect(
      api.batchCreateRuns(QUALITY_PROJECT_ID, {
        subject_type: "EPISODE",
        subject_ids: [QUALITY_EPISODE_ID, QUALITY_OTHER_EPISODE_ID],
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("响应条目与请求的对象集合不一致时拒绝（防串对象展示）", async () => {
    const { api } = makeApi(() =>
      jsonResponse([queuedQualityRunFixture()]),
    );

    await expect(
      api.batchCreateRuns(QUALITY_PROJECT_ID, {
        subject_type: "EPISODE",
        subject_ids: [QUALITY_OTHER_EPISODE_ID],
      }),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("空对象列表在发请求前拒绝", async () => {
    const { api, fetchImpl } = makeApi(() => jsonResponse([]));

    await expect(
      api.batchCreateRuns(QUALITY_PROJECT_ID, {
        subject_type: "EPISODE",
        subject_ids: [],
      }),
    ).rejects.toThrow(/至少/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("差异报告 GET diff 路径正确并校验 baseline/candidate 与请求一致", async () => {
    const { api, fetchImpl } = makeApi(() =>
      jsonResponse(qualityRunDiffFixture()),
    );

    const report = await api.getRunDiff(
      QUALITY_PROJECT_ID,
      QUALITY_BASELINE_RUN_ID,
      QUALITY_CANDIDATE_RUN_ID,
    );

    expect(report.has_regression).toBe(true);
    const [url] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(
      `/api/v1/projects/${QUALITY_PROJECT_ID}/quality-runs/${QUALITY_BASELINE_RUN_ID}/diff/${QUALITY_CANDIDATE_RUN_ID}`,
    );
  });

  it("差异报告返回的 candidate 与请求不一致时拒绝", async () => {
    const { api } = makeApi(() =>
      jsonResponse(
        qualityRunDiffFixture({
          candidate_run_id: "99999999-9999-4999-8999-999999999999",
        }),
      ),
    );

    await expect(
      api.getRunDiff(
        QUALITY_PROJECT_ID,
        QUALITY_BASELINE_RUN_ID,
        QUALITY_CANDIDATE_RUN_ID,
      ),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });
});
