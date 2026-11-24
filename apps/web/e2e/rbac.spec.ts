/**
 * V51-013｜RBAC 多角色浏览器 E2E（蓝图 `15_RBAC多角色浏览器E2E.md` §3）。
 *
 * **每个用例都同时断言 UI 行为与服务端拒绝**：蓝图明确反对"只验证按钮隐藏"
 * ——按钮隐藏可能只是前端推断，改 URL 或直接发请求仍能越权。因此每个场景都
 * 先走真实浏览器路径，再用页面同源 `request` 直连 API 断言 403/404，并对
 * "授权身份可读 / 越权身份被拒"做**成对**断言：只断言后者时，404 也可能
 * 因为资源根本不存在而出现，证明不了隔离。
 *
 * 数据来自同一套种子 `apps/web/scripts/e2e/seed_e2e.py`（`run-local-stack.sh`
 * 导出 ID），不另建种子体系：
 *
 * - 项目 A：owner（提交人 / 审核人）、editor（标注人）、viewer（只读）；
 * - 项目 B：只有 `OUTSIDER_ID`，A 的任何成员都不是 B 的成员；
 * - 两个项目各有一条采集任务、标注 Schema、数据集与数据集版本。
 *
 * 角色映射只用仓库既有 `ProjectRole`（viewer / editor / owner）与后端
 * `ROLE_PERMISSIONS`，不新增角色。
 */
import { Buffer } from "node:buffer";
import { createHmac } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

import {
  e2eEnv,
  expectApiRejected,
  fetchAs,
  rbacEnv,
  requireEnv,
  skipWithoutSeed,
} from "./support";

/** 与 `seed_e2e.py` 的 `main()` 一致：关键路径种子。 */
const CORE_SEED = [
  "DDP_E2E_PROJECT_ID",
  "DDP_E2E_TASK_ID",
  "DDP_E2E_IMPORT_ID",
  "DDP_E2E_REVIEWER_ID",
  "DDP_E2E_MCAP_PATH",
] as const;

/** V51-013 追加的多角色 / 跨项目种子。 */
const RBAC_SEED = [
  "DDP_E2E_PROJECT_B_ID",
  "DDP_E2E_ANNOTATOR_ID",
  "DDP_E2E_VIEWER_ID",
  "DDP_E2E_OUTSIDER_ID",
  "DDP_E2E_TASK_B_ID",
  "DDP_E2E_SCHEMA_A_ID",
  "DDP_E2E_SCHEMA_B_ID",
  "DDP_E2E_DATASET_A_ID",
  "DDP_E2E_DATASET_B_ID",
  "DDP_E2E_DATASET_VERSION_A_ID",
  "DDP_E2E_DATASET_VERSION_B_ID",
] as const;

/** 项目名（与种子常量一致，仅用于 UI 断言）。 */
const PROJECT_A_NAME = "DDP E2E 关键路径项目";
const PROJECT_B_NAME = "DDP E2E 隔离对照项目 B";
const TASK_B_NAME = "E2E 隔离对照任务 B";
const SCHEMA_B_CODE = "e2e-rbac-schema-b";
const DATASET_B_CODE = "e2e-rbac-ds-b";

/** 以指定身份进入页面：本地栈用 `X-User-Id` 注入身份（仅 local + loopback 生效）。 */
async function actAs(page: Page, userId: string): Promise<void> {
  await page.setExtraHTTPHeaders({ "X-User-Id": userId });
}

/** 断言页面上不出现某个业务字段（权限被拒后不得渲染上次成功的数据）。 */
async function expectAbsent(page: Page, text: string): Promise<void> {
  await expect(page.getByText(text)).toHaveCount(0);
}

/** 统一的"服务端拒绝可见"断言：403 → 无权访问；404 → 加载失败 + 稳定错误码。 */
async function expectRejectionVisible(
  page: Page,
  status: number,
): Promise<void> {
  if (status === 403) {
    await expect(page.getByText("无权访问")).toBeVisible({ timeout: 30_000 });
    return;
  }
  await expect(page.getByText("加载失败")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/resource_not_found/)).toBeVisible();
}

// ---------------------------------------------------------------------------
// 1. 登录后菜单/列表范围：不同角色看到的项目范围不同
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：列表范围按身份收敛", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("项目列表只显示当前身份可见的项目，服务端返回同一范围", async ({
    page,
  }) => {
    const env = rbacEnv();

    // --- A 的 owner：只看到 A（UI + API 双证） ---
    await actAs(page, env.ownerId);
    await page.goto("/projects");
    await expect(page.getByText(PROJECT_A_NAME)).toBeVisible({
      timeout: 30_000,
    });
    await expectAbsent(page, PROJECT_B_NAME);
    const ownerProjects = await fetchAs<{ items: { project_id: string }[] }>(
      page,
      env.ownerId,
      "/api/v1/projects",
    );
    expect(ownerProjects.items.map((item) => item.project_id)).toEqual([
      env.projectA,
    ]);

    // --- B 的 owner：只看到 B ---
    await actAs(page, env.outsiderId);
    await page.goto("/projects");
    await expect(page.getByText(PROJECT_B_NAME)).toBeVisible({
      timeout: 30_000,
    });
    await expectAbsent(page, PROJECT_A_NAME);
    const outsiderProjects = await fetchAs<{
      items: { project_id: string }[];
    }>(page, env.outsiderId, "/api/v1/projects");
    expect(outsiderProjects.items.map((item) => item.project_id)).toEqual([
      env.projectB,
    ]);

    // --- 只读成员仍在 A 内：列表可见（可见性不等于可写，见用例 3/4） ---
    await actAs(page, env.viewerId);
    await page.goto("/projects");
    await expect(page.getByText(PROJECT_A_NAME)).toBeVisible({
      timeout: 30_000,
    });
    await expectAbsent(page, PROJECT_B_NAME);

    // 关键：A 的成员用 B 的 project_id 直接查项目详情必须被拒（不泄露存在性）。
    const crossProject = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/projects/${env.projectB}`,
      userId: env.ownerId,
      forbidInBody: [PROJECT_B_NAME],
    });
    expect([403, 404]).toContain(crossProject.status);
  });
});

// ---------------------------------------------------------------------------
// 2. 直接访问未授权 URL 被拒绝：改 URL 里的 project_id 不能跨项目读
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：URL 跨项目改写", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("把 project_id 改成自己的项目读不到别的项目的任务", async ({ page }) => {
    const env = rbacEnv();

    // 正例：A 的真实任务在 A 下可读（证明下面的拒绝来自隔离而不是路径写错）。
    await actAs(page, env.ownerId);
    await page.goto(
      `/collection/tasks/${env.taskA}?project_id=${env.projectA}`,
    );
    await expect(page.getByText("可执行动作")).toBeVisible({ timeout: 30_000 });

    // 越权：B 的任务 ID + A 的 project_id（"改 URL 里的 project_id"）。
    await page.goto(
      `/collection/tasks/${env.taskB}?project_id=${env.projectA}`,
    );
    await expect(page.getByText("任务不存在或无权查看")).toBeVisible({
      timeout: 30_000,
    });
    await expectAbsent(page, TASK_B_NAME);

    const crossTask = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/projects/${env.projectA}/collection-tasks/${env.taskB}`,
      userId: env.ownerId,
      forbidInBody: [TASK_B_NAME, "E2E-TASK-B"],
    });
    expect(crossTask.status).toBe(404);

    // 越权：用 B 的 project_id 访问成员列表（A 的 owner 不是 B 的成员）。
    await actAs(page, env.ownerId);
    await page.goto(`/people?project_id=${env.projectB}`);
    await expect(page.getByText("未选择项目")).toBeVisible({ timeout: 30_000 });
    await expectAbsent(page, "E2E 对照项目 B 负责人");

    const crossMembers = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/projects/${env.projectB}/members`,
      userId: env.ownerId,
      forbidInBody: ["E2E 对照项目 B 负责人", env.outsiderId],
    });
    expect(crossMembers.status).toBe(403);

    // 成对基线：同一请求由 B 的 owner 发出必须 200。
    const ownMembers = await fetchAs<{ items: unknown[] }>(
      page,
      env.outsiderId,
      `/api/v1/projects/${env.projectB}/members`,
    );
    expect(ownMembers.items.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// 3. 只读成员（viewer）：可见但不可写
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：只读成员不可写", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("成员列表可读但无管理入口，写接口被服务端 403", async ({ page }) => {
    const env = rbacEnv();
    await actAs(page, env.viewerId);

    // UI：成员列表可读（READ 允许），但"添加成员"禁用、行内没有"移除"。
    await page.goto(`/people?project_id=${env.projectA}`);
    await expect(
      page.getByRole("heading", { name: "人员与团队" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "添加成员" })).toBeDisabled();
    await expect(page.getByRole("button", { name: /移除/ })).toHaveCount(0);
    // 前端明确标注"按钮隐藏只是提示，服务端仍会校验"。
    await expect(page.getByText("当前角色只读")).toBeVisible();

    // 服务端三项写操作都必须是 403 —— 不是按钮隐藏，而是请求被拒。
    const addMember = await expectApiRejected(page, {
      method: "POST",
      path: `/api/v1/projects/${env.projectA}/members`,
      userId: env.viewerId,
      body: { user_id: env.outsiderId, role: "viewer" },
    });
    expect(addMember.status).toBe(403);

    const patchProject = await expectApiRejected(page, {
      method: "PATCH",
      path: `/api/v1/projects/${env.projectA}`,
      userId: env.viewerId,
      headers: { "If-Match": '"1"' },
      body: { description: "viewer 越权尝试" },
    });
    expect(patchProject.status).toBe(403);

    const createTask = await expectApiRejected(page, {
      method: "POST",
      path: `/api/v1/projects/${env.projectA}/collection-tasks`,
      userId: env.viewerId,
      body: {
        code: "RBAC-VIEWER-DENIED",
        name: "viewer 越权创建",
        priority: "P2",
        target_episode_count: 1,
        target_duration_sec: 60,
      },
    });
    expect(createTask.status).toBe(403);

    // 成对基线：同样的写请求，editor（WRITE 允许）不会被权限拦住。
    const editorCreate = await page.request.post(
      `/api/v1/projects/${env.projectA}/collection-tasks`,
      {
        headers: { "X-User-Id": env.annotatorId },
        data: {
          code: "RBAC-EDITOR-ALLOWED",
          name: "editor 允许创建（证明上面的 403 来自角色而非路由）",
          priority: "P2",
          target_episode_count: 1,
          target_duration_sec: 60,
        },
        failOnStatusCode: false,
      },
    );
    expect(editorCreate.status()).toBe(201);
  });
});

// ---------------------------------------------------------------------------
// 4. 编辑者（editor）不能调用管理 / 删除接口
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：编辑者不可管理", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("成员管理与删除接口对 editor 403，owner 通过权限后是资源不存在", async ({
    page,
  }) => {
    const env = rbacEnv();
    await actAs(page, env.annotatorId);

    // UI：成员列表可读，但没有增删改入口，且页面直接说明只读。
    await page.goto(`/people?project_id=${env.projectA}`);
    await expect(page.getByRole("button", { name: "添加成员" })).toBeDisabled();
    await expect(page.getByRole("button", { name: /移除/ })).toHaveCount(0);
    await expect(page.getByText("当前角色只读")).toBeVisible();

    // 服务端：MANAGE_MEMBERS（加成员 / 改角色）拒绝 editor。
    const addMember = await expectApiRejected(page, {
      method: "POST",
      path: `/api/v1/projects/${env.projectA}/members`,
      userId: env.annotatorId,
      body: { user_id: env.outsiderId, role: "viewer" },
    });
    expect(addMember.status).toBe(403);

    const changeRole = await expectApiRejected(page, {
      method: "PATCH",
      path: `/api/v1/projects/${env.projectA}/members/${env.viewerId}`,
      userId: env.annotatorId,
      body: { role: "owner" },
    });
    expect(changeRole.status).toBe(403);

    // 服务端：DELETE 作用域只有 owner —— editor 在权限层就被拒。
    const editorDelete = await expectApiRejected(page, {
      method: "DELETE",
      path: `/api/v1/projects/${env.projectA}/collection-plans/${env.taskB}`,
      userId: env.annotatorId,
    });
    expect(editorDelete.status).toBe(403);

    // 成对基线：owner 的同一删除请求通过权限层，失败原因是资源不存在（404）。
    const ownerDelete = await page.request.delete(
      `/api/v1/projects/${env.projectA}/collection-plans/${env.taskB}`,
      {
        headers: { "X-User-Id": env.ownerId },
        failOnStatusCode: false,
      },
    );
    expect(ownerDelete.status()).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// 5. 跨项目资源 ID 猜测不可越权（Schema / DatasetVersion / 下载 URL）
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：跨项目资源 ID 猜测", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("猜到的 Schema / DatasetVersion / 下载 URL 在别的项目下被拒绝", async ({
    page,
  }) => {
    const env = rbacEnv();

    // 成对基线：A 的 Schema 在 A 下可读，B 的版本在 B 下可读。
    const ownSchema = await fetchAs<{ code: string }>(
      page,
      env.ownerId,
      `/api/v1/projects/${env.projectA}/annotation-schemas/${env.schemaA}`,
    );
    expect(ownSchema.code).toBe("e2e-rbac-schema-a");
    const ownVersion = await fetchAs<{ dataset_version_id: string }>(
      page,
      env.outsiderId,
      `/api/v1/projects/${env.projectB}/datasets/${env.datasetB}/versions/${env.datasetVersionB}`,
    );
    expect(ownVersion.dataset_version_id).toBe(env.datasetVersionB);

    // 越权 1：A 的身份 + B 的 Schema ID（浏览器路径）。
    await actAs(page, env.ownerId);
    await page.goto(
      `/annotation/schemas/${env.schemaB}?project_id=${env.projectA}`,
    );
    await expectRejectionVisible(page, 404);
    await expectAbsent(page, SCHEMA_B_CODE);

    const crossSchema = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/projects/${env.projectA}/annotation-schemas/${env.schemaB}`,
      userId: env.ownerId,
      forbidInBody: [SCHEMA_B_CODE, "E2E RBAC Schema B"],
    });
    expect(crossSchema.status).toBe(404);

    // 越权 2：A 的 project_id + B 的 dataset_id / version_id。
    await page.goto(
      `/datasets/${env.datasetB}/versions/${env.datasetVersionB}?project_id=${env.projectA}`,
    );
    await expectRejectionVisible(page, 404);
    await expectAbsent(page, DATASET_B_CODE);

    const crossVersion = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/projects/${env.projectA}/datasets/${env.datasetB}/versions/${env.datasetVersionB}`,
      userId: env.ownerId,
      forbidInBody: [DATASET_B_CODE, env.datasetVersionB],
    });
    expect(crossVersion.status).toBe(404);

    // 越权 3：下载签名 URL 不能越权（蓝图 §3 明确列出）。
    // 用 B 的 project_id：A 的 owner 不是 B 成员 → 403，且不返回任何签名。
    const crossDownload = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/raw-assets/${env.datasetVersionB}/download-url?project_id=${env.projectB}`,
      userId: env.ownerId,
      forbidInBody: ["X-Amz-Signature", "http"],
    });
    expect(crossDownload.status).toBe(403);

    // 成对基线：同一资产 ID 在自己的项目下通过权限层，失败原因是资源不存在。
    const ownDownload = await expectApiRejected(page, {
      method: "GET",
      path: `/api/v1/raw-assets/${env.datasetVersionB}/download-url?project_id=${env.projectA}`,
      userId: env.ownerId,
    });
    expect(ownDownload.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// 6. Collector 不能执行 Reviewer 操作（含职责分离）
// ---------------------------------------------------------------------------
test.describe("V51-013 多角色：复核职责分离", () => {
  skipWithoutSeed([...CORE_SEED, ...RBAC_SEED], "RBAC 需要多角色种子");

  test("只读身份无审核入口且裁决被 403；提交人自审被服务端拒绝", async ({
    page,
  }) => {
    const env = rbacEnv();
    const e2e = e2eEnv();
    const pagePath = `/data-source-imports/${e2e.importId}/review?project_id=${env.projectA}`;
    const decisionPath = `/api/v1/projects/${env.projectA}/data-source-imports/${e2e.importId}/review/decisions`;

    // --- viewer：UI 明确"无审核权限"，且没有裁决按钮 ---
    await actAs(page, env.viewerId);
    await page.goto(pagePath);
    await expect(page.getByText("无审核权限")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: /^通\s*过$/ })).toHaveCount(0);

    // 只读身份的裁决请求在写作用域就被 403（不是"按钮没渲染所以点不到"）。
    const viewerDecide = await expectApiRejected(page, {
      method: "POST",
      path: decisionPath,
      userId: env.viewerId,
      headers: { "If-Match": '"1"' },
      body: { decision: "ACCEPT" },
    });
    expect(viewerDecide.status).toBe(403);

    // --- 提交人本人：有写权限，但不得审自己的导入（职责分离） ---
    await actAs(page, env.ownerId);
    await page.goto(pagePath);
    await expect(page.getByText("待审核")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: /^通\s*过$/ }).click();
    // UI 必须显示出错提示，且状态没有被本地乐观更新。
    await expect(page.locator(".ant-message")).toContainText("数据源域规则校验失败", {
      timeout: 20_000,
    });
    await expect(page.getByText("待审核")).toBeVisible();

    const selfDecide = await page.request.post(decisionPath, {
      headers: {
        "X-User-Id": env.ownerId,
        "If-Match": '"1"',
        "Content-Type": "application/json",
      },
      data: JSON.stringify({ decision: "ACCEPT" }),
      failOnStatusCode: false,
    });
    expect(selfDecide.status()).toBe(422);
    const selfBody = await selfDecide.text();
    // 拒绝路径不得泄漏内部实现细节，也不得出现"已通过"。
    expect(selfBody).not.toContain("APPROVED");
    expect(selfBody).not.toContain("submitted_by");

    // 权威状态仍是 PENDING：拒绝是"未生效"，不是"先成功后回滚"。
    const review = await fetchAs<{ status: string }>(
      page,
      env.reviewerId,
      `/api/v1/projects/${env.projectA}/data-source-imports/${e2e.importId}/review`,
    );
    expect(review.status).toBe("PENDING");
  });
});

// ---------------------------------------------------------------------------
// 7. 会话过期后敏感操作失败
// ---------------------------------------------------------------------------
function base64url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

/** 按后端同一套 HS256 规则签发令牌（参数由启动脚本注入，不写死密钥）。 */
function signJwt(payload: Record<string, unknown>): string {
  const secret = requireEnv("DDP_E2E_JWT_SECRET");
  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = base64url(JSON.stringify(payload));
  const signature = createHmac("sha256", secret)
    .update(`${header}.${body}`)
    .digest("base64url");
  return `${header}.${body}.${signature}`;
}

test.describe("V51-013 多角色：会话过期", () => {
  skipWithoutSeed(
    [...CORE_SEED, ...RBAC_SEED, "DDP_E2E_JWT_SECRET"],
    "会话过期验证需要启动脚本注入的本地 JWT 密钥",
  );

  test("过期令牌的敏感操作返回 401，页面不渲染受保护数据", async ({ page }) => {
    const env = rbacEnv();
    const now = Math.floor(Date.now() / 1000);
    const claims = {
      sub: env.ownerId,
      platform_role: "member",
      iss: process.env.DDP_E2E_JWT_ISSUER?.trim() || "ddp",
      aud: process.env.DDP_E2E_JWT_AUDIENCE?.trim() || "ddp-api",
      iat: now - 7200,
      exp: now - 3600,
    };
    const taskBody = {
      code: "RBAC-TOKEN-DENIED",
      name: "令牌校验用例（不应落库）",
      priority: "P2",
      target_episode_count: 1,
      target_duration_sec: 60,
    };

    // 服务端：过期令牌的**敏感写操作**必须是 401（认证先于授权）。
    const expiredResponse = await page.request.post(
      `/api/v1/projects/${env.projectA}/collection-tasks`,
      {
        headers: {
          Authorization: `Bearer ${signJwt(claims)}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify(taskBody),
        failOnStatusCode: false,
      },
    );
    expect(expiredResponse.status()).toBe(401);
    const expiredBody = await expiredResponse.text();
    // 后端对外只有一句统一措辞，**不区分**"签名错误 / 已过期 / 格式错误"，
    // 也不回显项目 ID、用户 ID 等内部信息。
    expect(expiredBody).toContain("无效或已过期的访问令牌");
    expect(expiredBody).not.toContain(env.projectA);
    expect(expiredBody).not.toContain("exp");

    // 成对基线：同一身份的有效令牌可正常创建（证明 401 来自令牌过期）。
    const validResponse = await page.request.post(
      `/api/v1/projects/${env.projectA}/collection-tasks`,
      {
        headers: {
          Authorization: `Bearer ${signJwt({
            ...claims,
            iat: now,
            exp: now + 3600,
          })}`,
          "Content-Type": "application/json",
        },
        data: JSON.stringify({ ...taskBody, code: "RBAC-TOKEN-ALLOWED" }),
        failOnStatusCode: false,
      },
    );
    expect(validResponse.status()).toBe(201);

    // 浏览器真实路径：把过期令牌放进会话存储，页面请求必须失败且不渲染数据。
    await actAs(page, env.ownerId);
    await page.goto("/projects");
    const expired = signJwt(claims);
    await page.evaluate((token) => {
      sessionStorage.setItem("ddp.access_token", token);
    }, expired);
    await page.goto(
      `/collection/tasks/${env.taskA}?project_id=${env.projectA}`,
    );
    await expect(page.getByText("加载失败")).toBeVisible({ timeout: 30_000 });
    await expectAbsent(page, "可执行动作");

    // 清理令牌，避免影响后续（同 worker 内复用的浏览器上下文）。
    await page.evaluate(() => {
      sessionStorage.removeItem("ddp.access_token");
    });
  });
});
