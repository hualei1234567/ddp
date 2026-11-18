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
  stage: null,
  processed_items: null,
  total_items: null,
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

  it("登记时可显式请求远端校验", async () => {
    const { api, fetchImpl } = client(() => jsonResponse(sourceBody, 201));

    await api.createDataSource(PROJECT, {
      code: "hf-demo",
      name: "示例数据集",
      repo_id: "lerobot/aloha_sim_insertion_human",
      revision: SHA,
      license: "Apache-2.0",
      access_condition: "PUBLIC",
      verify_remote: true,
    });

    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body)).verify_remote).toBe(true);
  });

  it("详情路径带 import_limit 并解析校验结论", async () => {
    const detailBody = {
      data_source: sourceBody,
      imports: [importBody],
      remote_check: {
        status: "VERIFIED",
        checked_at: "2026-11-18T00:00:00Z",
        resolved_revision: SHA,
        license: "Apache-2.0",
        is_gated: false,
        dataset_card_url:
          "https://huggingface.co/datasets/lerobot/aloha_sim_insertion_human",
      },
    };
    const { api, fetchImpl } = client(() => jsonResponse(detailBody));

    const detail = await api.getDataSourceDetail(PROJECT, SOURCE_ID, {
      importLimit: 5,
    });

    expect(detail.remote_check?.resolved_revision).toBe(SHA);
    expect(detail.imports).toHaveLength(1);
    const [url] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-sources/${SOURCE_ID}/detail`);
    expect(url).toContain("import_limit=5");
  });

  it("详情中不属于该数据源的导入任务被拒绝", async () => {
    const { api } = client(() =>
      jsonResponse({
        data_source: sourceBody,
        imports: [
          {
            ...importBody,
            data_source_id: "99999999-9999-9999-9999-999999999999",
          },
        ],
        remote_check: null,
      }),
    );

    await expect(api.getDataSourceDetail(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });

  it("详情中的校验摘要 revision 非 sha 时被拒绝", async () => {
    const { api } = client(() =>
      jsonResponse({
        data_source: sourceBody,
        imports: [],
        remote_check: {
          status: "VERIFIED",
          checked_at: "2026-11-18T00:00:00Z",
          resolved_revision: "main",
          license: null,
          is_gated: false,
          dataset_card_url: null,
        },
      }),
    );

    await expect(api.getDataSourceDetail(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });
});

// --- 媒体资产与播放 ---------------------------------------------------------

const assetBody = {
  asset_id: SOURCE_ID,
  project_id: PROJECT,
  data_source_id: SOURCE_ID,
  import_id: IMPORT_ID,
  episode_key: "demo_001",
  path: "videos/demo_001_main.mp4",
  camera: "main",
  object_key: `data-sources/${PROJECT}/${SOURCE_ID}/v.mp4`,
  size_bytes: 754668,
  sha256: `sha256:${"a".repeat(64)}`,
  content_type: "video/mp4",
  task_name: "Sew lace fabric",
  excerpt_start_seconds: 69.0,
  excerpt_seconds: 12.0,
  source_fps: 30.0,
  created_at: "2026-11-15T00:00:00Z",
};

describe("媒体资产", () => {
  it("列出资产的路径与解析", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ items: [assetBody] }),
    );

    const items = await api.listImportAssets(PROJECT, IMPORT_ID);

    expect(items).toHaveLength(1);
    expect(items[0]?.camera).toBe("main");
    expect(items[0]?.excerpt_start_seconds).toBe(69.0);
    const [url] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-source-imports/${IMPORT_ID}/assets`);
  });

  it("未知机位不被丢弃", async () => {
    const { api } = client(() =>
      jsonResponse({ items: [{ ...assetBody, camera: "overhead_ceiling" }] }),
    );

    const items = await api.listImportAssets(PROJECT, IMPORT_ID);

    // 服务端可返回新机位；前端保留而不是判为非法。
    expect(items[0]?.camera).toBe("overhead_ceiling");
  });

  it("sha256 格式错误时解析失败", async () => {
    const { api } = client(() =>
      jsonResponse({ items: [{ ...assetBody, sha256: "sha256:short" }] }),
    );

    await expect(api.listImportAssets(PROJECT, IMPORT_ID)).rejects.toThrow();
  });

  it("播放用 POST 签发并校验签名参数", async () => {
    const url =
      "http://127.0.0.1:9000/ddp-raw/v.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256" +
      "&X-Amz-Signature=abc";
    const { api, fetchImpl } = client(() =>
      jsonResponse({
        asset_id: SOURCE_ID,
        url,
        expires_in_seconds: 900,
        content_type: "video/mp4",
        size_bytes: 754668,
      }),
    );

    const playback = await api.playback(PROJECT, SOURCE_ID);

    expect(playback.url).toBe(url);
    const [calledUrl, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toContain(`/media-assets/${SOURCE_ID}/playback`);
    expect(init.method).toBe("POST");
  });

  it("缺少签名的播放 URL 被拒绝", async () => {
    const { api } = client(() =>
      jsonResponse({
        asset_id: SOURCE_ID,
        url: "http://127.0.0.1:9000/ddp-raw/v.mp4",
        expires_in_seconds: 900,
        content_type: "video/mp4",
        size_bytes: 100,
      }),
    );

    await expect(api.playback(PROJECT, SOURCE_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });
});

const reviewBody = {
  review_id: "44444444-4444-4444-4444-444444444444",
  project_id: PROJECT,
  import_id: IMPORT_ID,
  status: "PENDING",
  decision: null,
  reason: null,
  reviewer_id: null,
  submitted_by: "55555555-5555-5555-5555-555555555555",
  created_at: "2026-11-15T00:00:00Z",
  decided_at: null,
  version: 1,
};

const reportBody = {
  import_id: IMPORT_ID,
  rule_version: "1.0.0",
  sample_scope: "IMPORT",
  total_assets: 39,
  total_episodes: 15,
  passed: true,
  findings: [
    { rule_id: "asset_present", passed: true, detail: "39 个资产均有对象键" },
  ],
  failures: [],
};

describe("导入审核与质量报告", () => {
  it("创建审核记录使用 POST", async () => {
    const { api, fetchImpl } = client(() => jsonResponse(reviewBody, 201));

    const review = await api.ensureReview(PROJECT, IMPORT_ID);

    expect(review.import_id).toBe(IMPORT_ID);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-source-imports/${IMPORT_ID}/review`);
    expect(init.method).toBe("POST");
  });

  it("审核记录不存在时返回 null 而不是抛错", async () => {
    const { api } = client(
      () =>
        new Response(
          JSON.stringify({
            error: { code: "not_found", message: "审核记录不存在", request_id: "r" },
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
    );

    await expect(api.getReview(PROJECT, IMPORT_ID)).resolves.toBeNull();
  });

  it("提交审核结论使用 POST + If-Match", async () => {
    const decided = {
      ...reviewBody,
      status: "APPROVED",
      decision: "ACCEPT",
      reviewer_id: "66666666-6666-6666-6666-666666666666",
      decided_at: "2026-11-15T01:00:00Z",
      version: 2,
    };
    const { api, fetchImpl } = client(() => jsonResponse(decided));

    const review = await api.decideReview(
      PROJECT,
      IMPORT_ID,
      { decision: "ACCEPT", reason: "抽查 3 条片段画面正常" },
      { version: 1 },
    );

    expect(review.status).toBe("APPROVED");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-source-imports/${IMPORT_ID}/review/decisions`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["If-Match"]).toBe('"1"');
    const body = JSON.parse(String(init.body));
    expect(body.decision).toBe("ACCEPT");
    expect(body.reason).toBe("抽查 3 条片段画面正常");
  });

  it("质量报告的 import_id 必须与请求一致", async () => {
    const { api } = client(() =>
      jsonResponse({
        ...reportBody,
        import_id: "77777777-7777-7777-7777-777777777777",
      }),
    );

    // client 把校验失败统一包装成 invalid_response；具体原因在 cause 里，
    // 这样页面能显示统一文案，排障时仍能拿到根因。
    await expect(api.qualityReport(PROJECT, IMPORT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
      cause: { message: expect.stringMatching(/import_id 与请求不一致/) },
    });
  });

  it("质量报告保留规则版本与失败明细", async () => {
    const failing = {
      ...reportBody,
      passed: false,
      failures: [
        {
          rule_id: "excerpt_within_duration",
          passed: false,
          detail: "片段 3 的摘录区间超出视频时长",
        },
      ],
    };
    const { api } = client(() => jsonResponse(failing));

    const report = await api.qualityReport(PROJECT, IMPORT_ID);

    expect(report.rule_version).toBe("1.0.0");
    expect(report.failures).toHaveLength(1);
    expect(report.failures[0]?.rule_id).toBe("excerpt_within_duration");
    expect(report.failures[0]?.passed).toBe(false);
  });
});

// --- 导入阶段/进度与步骤日志 -----------------------------------------------

const logBody = {
  log_id: "44444444-4444-4444-4444-444444444444",
  project_id: PROJECT,
  import_id: IMPORT_ID,
  stage: "INGEST",
  level: "INFO",
  message: "入库进度：3/10",
  attempt: 1,
  occurred_at: "2026-11-18T00:00:00Z",
};

describe("导入可观测性", () => {
  it("阶段与进度为 null 时保持 null（未测量），不补 0", async () => {
    const { api } = client(() => jsonResponse(importBody));

    const job = await api.getImport(PROJECT, IMPORT_ID);

    expect(job.stage).toBeNull();
    expect(job.processed_items).toBeNull();
    expect(job.total_items).toBeNull();
  });

  it("解析真实阶段与进度", async () => {
    const { api } = client(() =>
      jsonResponse({
        ...importBody,
        status: "RUNNING",
        stage: "INGEST",
        processed_items: 3,
        total_items: 10,
      }),
    );

    const job = await api.getImport(PROJECT, IMPORT_ID);

    expect(job.stage).toBe("INGEST");
    expect(job.processed_items).toBe(3);
    expect(job.total_items).toBe(10);
  });

  it("processed_items 超过 total_items 时拒绝解析", async () => {
    const { api } = client(() =>
      jsonResponse({
        ...importBody,
        stage: "INGEST",
        processed_items: 11,
        total_items: 10,
      }),
    );

    await expect(api.getImport(PROJECT, IMPORT_ID)).rejects.toThrow(
      /不符合契约/,
    );
  });

  it("读取日志走 /logs 并带 limit，解析 truncated 与 next_cursor", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({ items: [logBody], truncated: false, next_cursor: null }),
    );

    const result = await api.listImportLogs(PROJECT, IMPORT_ID, { limit: 200 });

    expect(result.truncated).toBe(false);
    expect(result.next_cursor).toBeNull();
    expect(result.items).toHaveLength(1);
    expect(result.items[0]?.stage).toBe("INGEST");
    expect(result.items[0]?.level).toBe("INFO");
    const [url] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(`/data-source-imports/${IMPORT_ID}/logs`);
    expect(url).toContain("limit=200");
  });

  it("加载更早日志时携带 cursor，并解析服务端返回的 next_cursor", async () => {
    const { api, fetchImpl } = client(() =>
      jsonResponse({
        items: [logBody],
        truncated: true,
        next_cursor: "older-page",
      }),
    );

    const result = await api.listImportLogs(PROJECT, IMPORT_ID, {
      limit: 100,
      cursor: "current-page",
    });

    expect(result.truncated).toBe(true);
    expect(result.next_cursor).toBe("older-page");
    const [url] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("cursor=current-page");
  });

  it("truncated 与 next_cursor 语义不一致时拒绝解析", async () => {
    const { api } = client(() =>
      jsonResponse({
        items: [],
        truncated: true,
        next_cursor: null,
      }),
    );

    await expect(
      api.listImportLogs(PROJECT, IMPORT_ID),
    ).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("日志的 import_id 与请求不一致时拒绝", async () => {
    const { api } = client(() =>
      jsonResponse({
        items: [
          {
            ...logBody,
            import_id: "77777777-7777-7777-7777-777777777777",
          },
        ],
        truncated: false,
        next_cursor: null,
      }),
    );

    await expect(api.listImportLogs(PROJECT, IMPORT_ID)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});
