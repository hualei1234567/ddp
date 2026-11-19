import { expect, test } from "@playwright/test";

import { e2eEnv, skipWithoutEnv } from "./support";

/**
 * 关键路径 1：上传。
 *
 * 覆盖：创建上传会话 → 选择文件 → 进度/失败可见。
 *
 * 走真实后端 + 真实 MinIO（`run-local-stack.sh` 起的临时栈）：
 * 选择文件后 `useUploadQueue` 会创建会话、逐片预签名 PUT、ack、complete，
 * 页面上的进度与最终状态都由服务端响应驱动，不使用任何假数据。
 */
test.describe("关键路径：上传", () => {
  skipWithoutEnv();

  test("创建会话、上传文件并展示进度与最终状态", async ({ page }) => {
    const env = e2eEnv();
    await page.goto(
      `/collection/workbench/${env.taskId}?project_id=${env.projectId}&step=upload`,
    );

    // 工作台先加载任务；上传步骤在任务就绪后渲染文件选择器。
    await expect(page.getByText("上传队列为空")).toBeVisible({ timeout: 30_000 });

    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeEnabled();

    await fileInput.setInputFiles({
      name: "e2e-episode.mcap",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("DDP E2E upload payload"),
    });

    // 队列项出现即说明 addFiles 已入队；后续状态由真实会话驱动。
    await expect(page.getByText("e2e-episode.mcap")).toBeVisible();

    // 至少出现过进度条（服务端返回 part_size 后计算真实百分比）。
    await expect(page.locator(".ant-progress")).toBeVisible({ timeout: 30_000 });

    // 终态：服务端登记（或去重命中）——两者都是"上传成功"的权威结论。
    await expect(
      page.getByText(/服务端已登记原始资产|服务端命中已有资产/),
    ).toBeVisible({ timeout: 60_000 });
  });
});
