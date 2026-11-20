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

  test("创建会话、上传文件并进入服务端校验", async ({ page }) => {
    const env = e2eEnv();
    await page.goto(
      `/collection/workbench/${env.taskId}?project_id=${env.projectId}&step=upload`,
    );

    // 工作台先加载任务；上传步骤在任务就绪后渲染文件选择器。
    await expect(page.getByText("上传队列为空")).toBeVisible({ timeout: 30_000 });

    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeEnabled();

    const createSessionResponse = page.waitForResponse((response) =>
      response.request().method() === "POST" &&
      response.url().includes("/upload-sessions") &&
      !response.url().includes("/actions/"),
    );
    await fileInput.setInputFiles(env.mcapPath);
    const uploadSession = (await (await createSessionResponse).json()) as {
      upload_session_id: string;
    };

    // 队列项出现即说明 addFiles 已入队；后续状态由真实会话驱动。
    await expect(page.getByText("e2e-episode.mcap")).toBeVisible();

    // 至少出现过进度条（服务端返回 part_size 后计算真实百分比）。
    await expect(page.locator(".ant-progress")).toBeVisible({ timeout: 30_000 });

    // complete 后先进入独立 Worker 校验，再通过刷新读取 Worker 权威终态。
    await expect(page.getByText("已提交，等待服务端校验")).toBeVisible({
      timeout: 30_000,
    });
    await expect(async () => {
      const sessionResponse = await page.request.get(
        `/api/v1/projects/${env.projectId}/upload-sessions/${uploadSession.upload_session_id}`,
      );
      expect(sessionResponse.ok()).toBe(true);
      const session = (await sessionResponse.json()) as {
        status: string;
        asset_id: string | null;
      };
      expect(session.status).toBe("COMPLETED");
      expect(session.asset_id).not.toBeNull();

      const assetResponse = await page.request.get(
        `/api/v1/projects/${env.projectId}/raw-assets/${session.asset_id}`,
      );
      expect(assetResponse.ok()).toBe(true);
      const asset = (await assetResponse.json()) as { status: string };
      expect(asset.status).toBe("VALID");
    }).toPass({ timeout: 30_000, intervals: [500, 1_000, 2_000] });
  });
});
