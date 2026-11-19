import { expect, test } from "@playwright/test";

import { e2eEnv, skipWithoutEnv } from "./support";

/**
 * 关键路径 2：审核。
 *
 * 覆盖：导入审看页 → 提交审核决定 → 状态更新。
 *
 * 种子里的审核记录由提交人（``88888888-...``）创建，本用例以审核人
 * （``99999999-...``）身份裁决，满足服务端的职责分离约束。审核页会先调用
 * `ensureReview`（幂等）再读取状态，因此断言的是服务端真实返回。
 */
test.describe("关键路径：审核", () => {
  skipWithoutEnv();

  test("提交审核决定后状态更新", async ({ page }) => {
    const env = e2eEnv();
    await page.goto(
      `/data-source-imports/${env.importId}/review?project_id=${env.projectId}`,
    );

    // 审核区块加载完成：待审核状态标签可见。
    await expect(page.getByText("待审核")).toBeVisible({ timeout: 30_000 });

    // 通过：无需原因，直接提交。
    await page.getByRole("button", { name: "通过", exact: true }).click();

    // 服务端返回后状态标签变为已通过，并出现审核结论。
    await expect(page.getByText("已通过")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("该审核已有结论")).toBeVisible();
  });
});
