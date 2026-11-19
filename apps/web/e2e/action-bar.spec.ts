import { expect, test } from "@playwright/test";

import { e2eEnv, skipWithoutEnv } from "./support";

/**
 * 关键路径 3：动作条。
 *
 * 覆盖：动作按钮只按服务端 `allowed_actions` 渲染，禁用/隐藏状态正确。
 *
 * 种子任务被真实 HTTP 动作推进到 ``IN_PROGRESS``，服务端下发的动作集合是
 * ``pause / block / submit / cancel`` 的子集。用例断言：
 * - 这些动作对应按钮存在；
 * - 状态机不允许的动作（如 ``start``、``resume``）不渲染；
 * - 详情页对 ``start`` 的禁用原因不影响其它动作的可用性。
 */
test.describe("关键路径：动作条", () => {
  skipWithoutEnv();

  test("只渲染服务端下发的动作，未下发动作不可见", async ({ page }) => {
    const env = e2eEnv();
    await page.goto(
      `/collection/tasks/${env.taskId}?project_id=${env.projectId}`,
    );

    // 等待任务详情加载完成（动作区标题）。
    await expect(page.getByText("可执行动作")).toBeVisible({ timeout: 30_000 });

    // IN_PROGRESS 允许的动作：暂停采集 / 标记阻塞 / 提交任务 / 取消任务。
    await expect(page.getByRole("button", { name: "暂停采集" })).toBeVisible();
    await expect(page.getByRole("button", { name: "提交任务" })).toBeVisible();

    // 状态机不允许的动作不得出现（前端不根据状态推断）。
    await expect(page.getByRole("button", { name: "开始采集" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "恢复采集" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "审批通过" })).toHaveCount(0);
  });

  test("需要原因的动作弹出确认框且未填原因时不可提交", async ({ page }) => {
    const env = e2eEnv();
    await page.goto(
      `/collection/tasks/${env.taskId}?project_id=${env.projectId}`,
    );

    await expect(page.getByText("可执行动作")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "暂停采集" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("填写原因")).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "确认提交" }),
    ).toBeDisabled();
  });
});
