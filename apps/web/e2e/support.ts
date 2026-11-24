import { test } from "@playwright/test";

/**
 * E2E 环境读取与跳过策略。
 *
 * - 未提供 `DDP_E2E_BASE_URL` 时，整组用例标记为 **skipped**（Playwright
 *   报告里是 skipped，不是 passed），并打印明确原因；
 * - 提供环境但缺少必要 ID 时直接失败，避免"以为跑了其实没跑"。
 */
export interface E2EEnv {
  projectId: string;
  taskId: string;
  importId: string;
  reviewerId: string;
  mcapPath: string;
}

function read(name: string): string {
  return process.env[name]?.trim() ?? "";
}

export function e2eEnv(): E2EEnv {
  const projectId = read("DDP_E2E_PROJECT_ID");
  const taskId = read("DDP_E2E_TASK_ID");
  const importId = read("DDP_E2E_IMPORT_ID");
  const reviewerId = read("DDP_E2E_REVIEWER_ID");
  const mcapPath = read("DDP_E2E_MCAP_PATH");
  if (!projectId || !taskId || !importId || !reviewerId || !mcapPath) {
    throw new Error(
      "缺少 DDP_E2E_PROJECT_ID / DDP_E2E_TASK_ID / DDP_E2E_IMPORT_ID / " +
        "DDP_E2E_REVIEWER_ID / DDP_E2E_MCAP_PATH；请用 npm run e2e:local 或按 " +
        "scripts/e2e/README.md 注入种子 ID",
    );
  }
  return { projectId, taskId, importId, reviewerId, mcapPath };
}

/**
 * 在 `test.describe` 内调用：无环境时把整组标记为跳过。
 *
 * Playwright 没有"整个文件级 skip 并保留报告"的配置项，`test.skip()` 在
 * describe 体内调用会把组内所有用例标记为 skipped，报告里原因可见。
 */
export function skipWithoutEnv(): void {
  if (!read("DDP_E2E_BASE_URL")) {
    test.skip(
      true,
      "SKIPPED: 未提供 DDP_E2E_BASE_URL（关键路径 E2E 需要可访问的后端与前端）",
    );
  }
}

/**
 * 在 `test.describe` 内调用：基础环境缺失或本组专属种子缺失时整组跳过。
 *
 * 与 `skipWithoutEnv` 的区别：本组除 `DDP_E2E_BASE_URL` 外还需要额外种子
 * （例如标注控制台需要一条 `IN_PROGRESS` 的分配）。缺种子时**必须是
 * skipped 而不是 failed**——本地栈模式按设计不提供这些种子，见
 * `scripts/e2e/README.md`；提供环境却缺种子时，用例体里的 `read()` 仍会
 * 直接失败，避免"以为跑了其实没跑"。
 */
export function skipWithoutSeed(names: readonly string[], reason: string): void {
  skipWithoutEnv();
  const missing = names.filter((name) => !read(name));
  if (missing.length > 0) {
    test.skip(true, `SKIPPED: ${reason}（缺少 ${missing.join(" / ")}）`);
  }
}

/**
 * V51-013 多角色 / 跨项目种子（`seed_e2e.py` 的 `E2E_IDS`）。
 *
 * 与 `e2eEnv()` 分开：本组需要**两个项目、四个角色**，本地栈模式已由同一套
 * 种子提供（见 `apps/web/scripts/e2e/seed_e2e.py` 的 `_seed_rbac_identity`），
 * 缺任何一个 ID 都直接失败——静默跳过会让"没证明"看起来像"已证明"。
 */
export interface RBACEnv {
  /** 项目 A：关键路径项目，成员含 owner/editor/viewer。 */
  projectA: string;
  /** 项目 B：隔离对照项目，A 的任何成员都不是它的成员。 */
  projectB: string;
  ownerId: string;
  reviewerId: string;
  annotatorId: string;
  viewerId: string;
  outsiderId: string;
  taskA: string;
  taskB: string;
  schemaA: string;
  schemaB: string;
  datasetA: string;
  datasetB: string;
  datasetVersionA: string;
  datasetVersionB: string;
}

/** 读取必需变量；缺失时抛错，让"以为跑了其实没跑"立刻暴露。 */
export function requireEnv(name: string): string {
  const value = read(name);
  if (!value) {
    throw new Error(
      `缺少 ${name}；RBAC E2E 的种子由 apps/web/scripts/e2e/seed_e2e.py 提供，` +
        "请用 npm run e2e:local（本地栈）或按 scripts/e2e/README.md 注入",
    );
  }
  return value;
}

export function rbacEnv(): RBACEnv {
  return {
    projectA: requireEnv("DDP_E2E_PROJECT_ID"),
    projectB: requireEnv("DDP_E2E_PROJECT_B_ID"),
    ownerId: requireEnv("DDP_E2E_USER_ID"),
    reviewerId: requireEnv("DDP_E2E_REVIEWER_ID"),
    annotatorId: requireEnv("DDP_E2E_ANNOTATOR_ID"),
    viewerId: requireEnv("DDP_E2E_VIEWER_ID"),
    outsiderId: requireEnv("DDP_E2E_OUTSIDER_ID"),
    taskA: requireEnv("DDP_E2E_TASK_ID"),
    taskB: requireEnv("DDP_E2E_TASK_B_ID"),
    schemaA: requireEnv("DDP_E2E_SCHEMA_A_ID"),
    schemaB: requireEnv("DDP_E2E_SCHEMA_B_ID"),
    datasetA: requireEnv("DDP_E2E_DATASET_A_ID"),
    datasetB: requireEnv("DDP_E2E_DATASET_B_ID"),
    datasetVersionA: requireEnv("DDP_E2E_DATASET_VERSION_A_ID"),
    datasetVersionB: requireEnv("DDP_E2E_DATASET_VERSION_B_ID"),
  };
}

/**
 * 断言"服务端拒绝"而不是"按钮隐藏"。
 *
 * 蓝图 §3 明确反对只验证按钮不可见：按钮隐藏可能只是前端推断，改 URL 或
 * 直接发请求仍能越权。本函数通过页面同源的 `request` 直接调用 API，
 * 断言状态码落在允许的拒绝集合里。
 */
export async function expectApiRejected(
  page: import("@playwright/test").Page,
  options: {
    method: "GET" | "POST" | "PATCH" | "DELETE";
    path: string;
    userId: string;
    body?: unknown;
    headers?: Record<string, string>;
    /** 允许的拒绝状态码；默认 403/404（按现有安全策略两者都可接受）。 */
    allowedStatus?: readonly number[];
    /** 附加断言：响应体不得泄漏被保护资源的关键内容。 */
    forbidInBody?: readonly string[];
  },
): Promise<{ status: number; body: string }> {
  const allowed = options.allowedStatus ?? [403, 404];
  const response = await page.request.fetch(options.path, {
    method: options.method,
    headers: {
      "X-User-Id": options.userId,
      ...(options.body === undefined
        ? {}
        : { "Content-Type": "application/json" }),
      ...options.headers,
    },
    data: options.body === undefined ? undefined : JSON.stringify(options.body),
    failOnStatusCode: false,
  });
  const body = await response.text();
  if (!allowed.includes(response.status())) {
    throw new Error(
      `期望服务端拒绝（${allowed.join("/")}），实际 ${response.status()}：` +
        `${options.method} ${options.path} → ${body.slice(0, 300)}`,
    );
  }
  // 出现 5xx 说明拒绝发生在异常路径上，不是授权判定。
  if (response.status() >= 500) {
    throw new Error(
      `服务端以 ${response.status()} 失败而非拒绝：${body.slice(0, 300)}`,
    );
  }
  for (const fragment of options.forbidInBody ?? []) {
    if (body.includes(fragment)) {
      throw new Error(
        `拒绝响应泄漏了受保护内容（${fragment}）：${body.slice(0, 300)}`,
      );
    }
  }
  return { status: response.status(), body };
}

/**
 * 以指定身份执行一次成功请求，返回解析后的 JSON。
 *
 * 用于"同一 ID 在授权身份下可读、在越权身份下被拒"的成对断言——
 * 只验证后者时，404 也可能因为资源根本不存在而出现，证明不了隔离。
 */
export async function fetchAs<T>(
  page: import("@playwright/test").Page,
  userId: string,
  path: string,
): Promise<T> {
  const response = await page.request.get(path, {
    headers: { "X-User-Id": userId },
    failOnStatusCode: false,
  });
  if (!response.ok()) {
    throw new Error(
      `以 ${userId} 读取 ${path} 失败：${response.status()} ` +
        `${(await response.text()).slice(0, 300)}`,
    );
  }
  return (await response.json()) as T;
}
