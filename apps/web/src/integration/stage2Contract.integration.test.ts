import { beforeAll, describe, expect, it } from "vitest";

import { ApiClient } from "../services/api";
import { createCollectionsApi, type CollectionsApi } from "../services/collections";
import { createUploadsApi, type UploadsApi } from "../services/uploads";

/**
 * 真实后端契约冒烟（不需要对象存储）。
 *
 * 用于在 MinIO 不可用时仍能验证：创建采集任务、任务列表、创建会话的 415 负向。
 * 默认单测跳过；启动方式见 scripts/integration/run-stage2-upload.sh --smoke。
 */
function readEnv(name: string): string {
  const runtime = globalThis as {
    process?: { env?: Record<string, string | undefined> };
  };
  return runtime.process?.env?.[name]?.trim() ?? "";
}

const BASE_URL = readEnv("DDP_IT_BASE_URL");
const PROJECT_ID = readEnv("DDP_IT_PROJECT_ID");
const USER_ID = readEnv("DDP_IT_USER_ID");

if (BASE_URL !== "" && (PROJECT_ID === "" || USER_ID === "")) {
  throw new Error(
    "DDP_IT_BASE_URL 已设置，但缺少 DDP_IT_PROJECT_ID 或 DDP_IT_USER_ID",
  );
}

const integration = BASE_URL === "" ? describe.skip : describe;

let collectionsApi!: CollectionsApi;
let uploadsApi!: UploadsApi;

integration("DDP-307 真实后端契约冒烟", () => {
  beforeAll(() => {
    const client = new ApiClient({
      baseUrl: BASE_URL,
      timeoutMs: 30_000,
      fetchImpl: (input, init) => {
        const headers = new Headers(init?.headers);
        headers.set("X-User-Id", USER_ID);
        return fetch(input, { ...init, headers });
      },
    });
    collectionsApi = createCollectionsApi(client);
    uploadsApi = createUploadsApi(client);
  });

  it("创建采集任务并在列表中可见", async () => {
    const code = `IT307-SMOKE-${Date.now().toString(36)}`;
    const task = await collectionsApi.createTask(
      PROJECT_ID,
      {
        code,
        name: "DDP-307 真实后端冒烟任务",
        priority: "P2",
        target_episode_count: 0,
        target_duration_sec: 0,
      },
      { idempotencyKey: crypto.randomUUID() },
    );
    expect(task.project_id).toBe(PROJECT_ID);
    expect(task.code).toBe(code);
    expect(task.status).toBe("DRAFT");

    const page = await collectionsApi.listTasks(PROJECT_ID, {
      status: "DRAFT",
    });
    expect(page.items.some((item) => item.task_id === task.task_id)).toBe(
      true,
    );
  });

  it("声明内容类型不在白名单返回 415", async () => {
    await expect(
      uploadsApi.createSession(
        PROJECT_ID,
        {
          original_filename: "smoke.txt",
          declared_content_type: "text/plain",
          declared_size_bytes: 256,
        },
        { idempotencyKey: crypto.randomUUID() },
      ),
    ).rejects.toMatchObject({
      status: 415,
      code: "file_type_unsupported",
    });
  });
});
