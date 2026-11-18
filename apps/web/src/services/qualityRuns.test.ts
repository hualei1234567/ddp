import { describe, expect, it, vi } from "vitest";

import {
  QUALITY_IMPORT_ID,
  QUALITY_PROJECT_ID,
  QUALITY_RUN_ID,
  qualityRunFindingFixture,
  qualityRunFixture,
} from "./__fixtures__/qualityRunFixtures";
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
});
