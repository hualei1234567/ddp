import { beforeAll, describe, expect, it } from "vitest";

import { ApiClient } from "../services/api";
import {
  createAnnotationApi,
  type AnnotationApi,
} from "../services/annotation";
import type {
  AnnotationSubmission,
  AnnotationTask,
  GoldSet,
} from "../types/annotation";
import type { CursorPage } from "../types/api";

/**
 * 阶段 3 标注真实后端只读集成测试。
 *
 * 只有显式设置 DDP_IT_BASE_URL、DDP_IT_PROJECT_ID、DDP_IT_USER_ID
 * 时才执行；否则整组跳过。测试只读，不创建/修改业务数据，可重复运行。
 * 身份通过后端本地 Header 认证（X-User-Id）注入，与阶段 2 集成测试一致。
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

function requireEnv(name: string, value: string): string {
  if (value === "") {
    throw new Error(`集成测试缺少环境变量：${name}`);
  }
  return value;
}

function createAuthenticatedClient(): ApiClient {
  return new ApiClient({
    baseUrl: requireEnv("DDP_IT_BASE_URL", BASE_URL),
    timeoutMs: 30_000,
    fetchImpl: (input, init) => {
      const headers = new Headers(init?.headers);
      headers.set("X-User-Id", requireEnv("DDP_IT_USER_ID", USER_ID));
      return fetch(input, { ...init, headers });
    },
  });
}

function assertScope<T extends { project_id: string }>(
  page: CursorPage<T>,
): void {
  expect(page.items.every((item) => item.project_id === PROJECT_ID)).toBe(true);
}

let api!: AnnotationApi;
let firstTask: AnnotationTask | null = null;
let firstGoldSet: GoldSet | null = null;

integration("DDP-319 阶段 3 标注真实后端只读联调", () => {
  beforeAll(async () => {
    api = createAnnotationApi(createAuthenticatedClient());
    const tasks = await api.listTasks(PROJECT_ID, { limit: 20 });
    firstTask = tasks.items[0] ?? null;
    const goldSets = await api.listGoldSets(PROJECT_ID, { limit: 20 });
    firstGoldSet = goldSets.items[0] ?? null;
  });

  it("Schema / 样本集 / 任务 / 黄金集列表按项目返回", async () => {
    const [schemas, sampleSets, tasks, goldSets] = await Promise.all([
      api.listSchemas(PROJECT_ID),
      api.listSampleSets(PROJECT_ID),
      api.listTasks(PROJECT_ID),
      api.listGoldSets(PROJECT_ID),
    ]);
    assertScope(schemas);
    assertScope(sampleSets);
    assertScope(tasks);
    assertScope(goldSets);
  });

  it("任务详情、批次、分配与提交可重复读取", async (context) => {
    if (!firstTask) {
      context.skip();
      return;
    }
    const task = await api.getTask(PROJECT_ID, firstTask.task_id);
    expect(task.task_id).toBe(firstTask.task_id);
    expect(task.project_id).toBe(PROJECT_ID);

    const batches = await api.listBatches(PROJECT_ID, task.task_id);
    assertScope(batches);
    const assignments = await api.listAssignments(PROJECT_ID, {
      taskId: task.task_id,
    });
    assertScope(assignments);
    const submissions = await api.listSubmissions(PROJECT_ID, task.task_id);
    assertScope(submissions);

    const firstSubmission: AnnotationSubmission | undefined =
      submissions.items[0];
    if (!firstSubmission) {
      context.skip();
      return;
    }
    const detail = await api.getSubmission(
      PROJECT_ID,
      firstSubmission.submission_id,
    );
    expect(detail.submission_id).toBe(firstSubmission.submission_id);
    expect(detail.project_id).toBe(PROJECT_ID);
    const reviews = await api.listSubmissionReviews(
      PROJECT_ID,
      detail.submission_id,
    );
    assertScope(reviews);
    const arbitrations = await api.listSubmissionArbitrations(
      PROJECT_ID,
      detail.submission_id,
    );
    assertScope(arbitrations);
  });

  it("黄金集详情默认脱敏；答案端点 403 或返回授权视图", async (context) => {
    if (!firstGoldSet) {
      context.skip();
      return;
    }
    const detail = await api.getGoldSet(PROJECT_ID, firstGoldSet.gold_set_id);
    expect(detail.project_id).toBe(PROJECT_ID);
    expect(
      detail.items.every(
        (item) =>
          item.answers_redacted === true &&
          item.expected_labels === undefined,
      ),
    ).toBe(true);

    try {
      const answers = await api.getGoldSetAnswers(
        PROJECT_ID,
        detail.gold_set_id,
      );
      expect(answers.project_id).toBe(PROJECT_ID);
      expect(answers.items.some((item) => item.expected_labels !== undefined)).toBe(
        true,
      );
    } catch (caught) {
      const error = caught as { status?: number; code?: string };
      expect(error.status).toBe(403);
      expect(error.code).toBe("gold_set_access_denied");
    }
  });

  it("IAA 与质检抽检按任务读取，样本不足不返回 0", async (context) => {
    if (!firstTask) {
      context.skip();
      return;
    }
    const [iaa, quality] = await Promise.all([
      api.listIAAReports(PROJECT_ID, firstTask.task_id),
      api.listQualitySamples(PROJECT_ID, firstTask.task_id),
    ]);
    assertScope(iaa);
    assertScope(quality);
    for (const report of iaa.items) {
      if (report.status === "INSUFFICIENT_DATA") {
        expect(
          report.metrics.every(
            (metric) => metric.value === null || metric.status !== "OK",
          ),
        ).toBe(true);
      }
    }
  });

  it("跨项目/不存在资源统一 404 或 403 语义", async () => {
    const missingId = crypto.randomUUID();
    await expect(api.getTask(PROJECT_ID, missingId)).rejects.toMatchObject({
      status: 404,
    });
    await expect(api.getSchema(PROJECT_ID, missingId)).rejects.toMatchObject({
      status: 404,
    });
  });
});
