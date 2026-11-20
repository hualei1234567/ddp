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
