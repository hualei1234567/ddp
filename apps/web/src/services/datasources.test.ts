/**
 * 数据源 client 的请求层测试：路径、查询参数、If-Match、幂等键与解析。
 *
 * 审计报告指出前端 4 个 client 缺请求层单测（R-5 附带项）；新 client 一并补上，
 * 避免"页面能跑但路径/头写错"这类缺陷只能靠手工发现。
 */

import { describe, expect, it, vi } from "vitest";

import type { CursorPage } from "../types/api";
import type { DataSource, DataSourceImport } from "../types/datasource";
import { ApiClient } from "./api";
import { createDataSourceApi } from "./datasources";

const PROJECT = "11111111-1111-1111-1111-111111111111";
const SOURCE_ID = "22222222-2222-2222-2222-222222222222";
const IMPORT_ID = "33333333-3333-3333-3333-333333333333";
const SHA = "0123456789abcdef0123456789abcdef01234567";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function client(handler: (url: string, init: RequestInit) => Response): {
  api: ReturnType<typeof createDataSourceApi>;
  fetchImpl: ReturnType<typeof vi.fn>;
} {
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init ?? {}),
  );
  const apiClient = new ApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
  return { api: createDataSourceApi(apiClient), fetchImpl };
}

const sourceBody: DataSource = {
  data_source_id: SOURCE_ID,
  project_id: PROJECT,
  code: "hf-demo",
  name: "示例数据集",
  kind: "HUGGINGFACE",
  repo_id: "lerobot/aloha_sim_insertion_human",
  revision: SHA,
  license: "Apache-2.0",
  access_condition: "PUBLIC",
  is_gated: false,
  config_name: null,
  split: null,
  dataset_card_url: null,
  notes: null,
  metadata: {},
  version: 1,
};

const importBody: DataSourceImport = {
  import_id: IMPORT_ID,
  project_id: PROJECT,
  data_source_id: SOURCE_ID,
  status: "PENDING",
  scope: "SAMPLE",
  tool_name: "huggingface_hub",
  tool_version: "0.27.1",
  sample_limit: 10,
  cache_location: null,
  imported_row_count: null,
  imported_file_count: null,
  error_message: null,
  started_at: null,
  finished_at: null,
  version: 1,
  allowed_actions: ["start", "cancel"],
};

describe("createDataSourceApi", () => {
  it("登记数据源使用 POST + Idempotency-Key", async () => {
    const { api, fetchImpl } = client(() => jsonResponse(sourceBody, 201));

    const created = await api.createDataSource(PROJECT, {
      code: "hf-demo",
      name: "示例数据集",
      repo_id: "lerobot/aloha_sim_insertion_human",
      revision: SHA,
      license: "Apache-2.0",
      access_condition: "PUBLIC",
    });

    expect(created.data_source_id).toBe(SOURCE_ID);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/api/v1/projects/${PROJECT}/data-sources`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(String(init.body)).revision).toBe(SHA);
  });

  it("改档使用 PATCH + If-Match + Idempotency-Key", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ ...sourceBody, license: "MIT", version: 2 }),
    );

    const updated = await api.updateDataSource(
      PROJECT,
      SOURCE_ID,
      { license: "MIT" },
      { version: 1 },
    );

    expect(updated.license).toBe("MIT");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-sources/${SOURCE_ID}`);
    expect(init.method).toBe("PATCH");
    const headers = init.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"1"');
    expect(headers["Idempotency-Key"]).toBeTruthy();
  });

  it("列表带 kind 与分页查询参数", async () => {
    const page: CursorPage<DataSource> = {
      items: [sourceBody],
      next_cursor: "next-1",
    };
    const { api, fetchImpl } = client(() => jsonResponse(page));

    const result = await api.listDataSources(PROJECT, {
      kind: "HUGGINGFACE",
      limit: 10,
    });

    expect(result.items).toHaveLength(1);
    expect(result.next_cursor).toBe("next-1");
    const [url] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("kind=HUGGINGFACE");
    expect(url).toContain("limit=10");
  });

  it("登记导入任务走数据源子路径", async () => {
    const { api, fetchImpl } = client(() => jsonResponse(importBody, 201));

    await api.createImport(PROJECT, SOURCE_ID, {
      scope: "SAMPLE",
      tool_name: "huggingface_hub",
      tool_version: "0.27.1",
      sample_limit: 10,
    });

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-sources/${SOURCE_ID}/imports`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body)).sample_limit).toBe(10);
  });

  it("推进导入状态带 If-Match 与错误信息", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ ...importBody, status: "FAILED", version: 2 }),
    );

    const failed = await api.actImport(PROJECT, IMPORT_ID, "fail", {
      version: 1,
      error_message: "网络超时",
    });

    expect(failed.status).toBe("FAILED");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-source-imports/${IMPORT_ID}/actions/fail`);
    const headers = init.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"1"');
    expect(JSON.parse(String(init.body)).error_message).toBe("网络超时");
  });

  it("服务端返回非 sha 的 revision 时解析失败", async () => {
    const { api } = client(() =>
      jsonResponse({ ...sourceBody, revision: "main" }),
    );

    // ApiClient 统一把校验失败收敛为契约错误文案，不向调用方泄漏内部细节。
    await expect(api.getDataSource(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });

  it("GATED 与 is_gated 不一致时解析失败", async () => {
    const { api } = client(() =>
      jsonResponse({
        ...sourceBody,
        access_condition: "GATED",
        is_gated: false,
      }),
    );

    await expect(api.getDataSource(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });

  it("跨项目响应被拒绝（服务端返回了别的 project_id）", async () => {
    const { api } = client(() =>
      jsonResponse({
        ...sourceBody,
        project_id: "99999999-9999-9999-9999-999999999999",
      }),
    );

    await expect(api.getDataSource(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约|项目/,
    );
  });
});
