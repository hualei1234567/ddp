import { defineConfig, devices } from "@playwright/test";

/**
 * DDP 前端关键路径 E2E（DDP-V21-N8-007）。
 *
 * 设计约束：
 * - **官方方案**：只使用 `@playwright/test`，不自研浏览器驱动；
 * - **无环境显式跳过**：未提供 `DDP_E2E_BASE_URL` 时，`e2e/run.mjs`
 *   会打印 `SKIPPED: 未提供 DDP_E2E_BASE_URL` 并退出 0；直接调用
 *   `playwright test` 时，各 spec 通过 `test.skip` 标记为 skipped
 *   （Playwright 报告里是 skipped，不是 passed，不会被误当成通过）；
 * - **不写死凭据**：身份通过 `DDP_E2E_USER_ID` 注入 `X-User-Id` 头，
 *   仅对本地回环后端有效（后端 `DDP_LOCAL_HEADER_AUTH=true` + loopback 才接受）。
 *
 * 启动方式见 `scripts/e2e/README.md`。
 */
const baseURL = process.env.DDP_E2E_BASE_URL?.trim() ?? "";
const userId = process.env.DDP_E2E_USER_ID?.trim() ?? "";

export default defineConfig({
  testDir: "./e2e",
  // 关键路径串行执行：三条路径共享同一份种子数据，并行会互相干扰。
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  // 本任务只做关键路径冒烟，不追求全量覆盖；失败时保留 trace 便于定位。
  reporter: [["list"], ["html", { outputFolder: "e2e-report", open: "never" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: baseURL || "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // 本地 Header 身份：只在 local + loopback 后端生效，生产环境会被拒绝。
    extraHTTPHeaders: userId ? { "X-User-Id": userId } : {},
    locale: "zh-CN",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
