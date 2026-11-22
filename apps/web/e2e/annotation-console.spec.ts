import { expect, test } from "@playwright/test";

/**
 * 关键路径：标注控制台（V31-027）。
 *
 * 覆盖：我的任务（个人队列） → 工作台 → 时间轴框选 → OBJECT_TRACK 行编辑器
 * → 必填标签 → 提交 → 复核队列 → 创建复核（默认当前用户）→ 领取 → 通过。
 *
 * 数据由真实后端提供：`DDP_E2E_ANNOTATION_ASSIGNMENT_ID` 指向一条已
 * `IN_PROGRESS` 的分配（见 `scripts/e2e/` 的真实链路种子）。未提供环境时
 * 整组 skipped，不会把"没跑"当成"通过"。
 */
import { skipWithoutSeed } from "./support";

/** 页面上同时存在多个已挂载但隐藏的下拉；只操作当前展开的那一个。 */
function visibleDropdown(page: import("@playwright/test").Page) {
  return page.locator(".ant-select-dropdown:visible");
}

function read(name: string): string {
  const value = process.env[name]?.trim() ?? "";
  if (!value) {
    throw new Error(`缺少 ${name}；请按 scripts/e2e/README.md 注入标注链路种子`);
  }
  return value;
}

test.describe("关键路径：标注控制台", () => {
  // 本组需要真实栈种子（含 Manifest 的 Episode + 一条 IN_PROGRESS 分配）：
  // 本地栈模式按设计不提供，缺种子时显式 skipped，报告里能看见原因。
  skipWithoutSeed(
    ["DDP_E2E_ANNOTATION_ASSIGNMENT_ID", "DDP_E2E_ANNOTATION_TASK_ID"],
    "标注控制台需要真实栈标注链路种子，见 scripts/e2e/README.md",
  );

  test("我的任务 → 工作台 → 轨迹与片段 → 提交", async ({ page }) => {
    const projectId = read("DDP_E2E_PROJECT_ID");
    const assignmentId = read("DDP_E2E_ANNOTATION_ASSIGNMENT_ID");

    // 1. 个人队列：按服务端认证身份聚合，必须有待处理条目。
    await page.goto(`/annotation/my-tasks?project_id=${projectId}`);
    await expect(page.getByRole("heading", { name: "我的任务" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/待处理（/)).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("link", { name: "打开工作台" }).first(),
    ).toBeVisible();

    // 2. 工作台：分配里的每个 Episode 各有一个编辑器，媒体与时间轴来自服务端。
    await page.goto(
      `/annotation/workbench/${assignmentId}?project_id=${projectId}`,
    );
    await expect(page.getByText(/标注工作台 ·/)).toBeVisible({
      timeout: 30_000,
    });
    const editors = page.getByTestId("episode-editor");
    await expect(editors.first()).toBeVisible({ timeout: 30_000 });
    const episodeCount = await editors.count();
    expect(episodeCount).toBeGreaterThan(0);

    // 3. 逐个 Episode：时间轴框选 + 必填标签（提交时服务端要求每条记录完整）。
    for (let index = 0; index < episodeCount; index += 1) {
      const editor = editors.nth(index);
      const timeline = editor.getByTestId("annotation-timeline");
      await expect(timeline).toBeVisible({ timeout: 30_000 });
      await timeline.scrollIntoViewIfNeeded();

      const box = await timeline.boundingBox();
      if (!box) {
        throw new Error(`第 ${index + 1} 个 Episode 的时间轴不可见，无法框选`);
      }
      await page.mouse.move(box.x + box.width * 0.2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, {
        steps: 8,
      });
      await page.mouse.up();
      await expect(editor.getByText("已框选")).toBeVisible();

      // 首个 Episode 额外验证 OBJECT_TRACK 结构化行编辑器：新增轨迹时带入选区。
      if (index === 0) {
        await editor.getByRole("button", { name: /新增轨迹/ }).click();
        await expect(
          editor.getByLabel(/轨迹 .+ 开始时间/).first(),
        ).not.toHaveValue("");
      }

      // 必填 1：Episode 结果。
      await editor.getByRole("combobox", { name: "episode_outcome" }).click();
      await visibleDropdown(page).getByTitle("success").click();

      // 必填 2：时间片段，用时间轴选区填入起止纳秒。
      await editor.getByRole("combobox", { name: "时间片段目标标签" }).click();
      await visibleDropdown(page).getByTitle("action_segment（TIME_RANGE）").click();
      await editor.getByRole("button", { name: "把选区填入标签" }).click();
      await expect(editor.getByText("已框选")).toBeHidden();
    }

    // 4. 提交；服务端返回后页面显示冻结提示。
    await page.getByRole("button", { name: /提交标注/ }).click();
    await expect(page.getByText(/提交已冻结/)).toBeVisible({ timeout: 30_000 });

    // 5. 复核：换成复核人身份，从复核队列进入该提交，领取并通过。
    const reviewerId = read("DDP_E2E_REVIEWER_ID");
    const taskId = read("DDP_E2E_ANNOTATION_TASK_ID");
    const userId = read("DDP_E2E_USER_ID");
    await page.setExtraHTTPHeaders({ "X-User-Id": reviewerId });
    await page.goto(
      `/annotation/reviews?project_id=${projectId}&task_id=${taskId}`,
    );
    await expect(page.getByRole("heading", { name: "复核队列" })).toBeVisible({
      timeout: 30_000,
    });
    await page.getByRole("link", { name: "查看 / 复核" }).first().click();
    const createReview = page.getByRole("button", { name: "创建复核" });
    await expect(createReview).toBeVisible({ timeout: 30_000 });
    // 复核人默认就是当前认证用户，不需要手抄 UUID。
    await expect(page.getByRole("combobox", { name: "复核人" })).toHaveValue(
      reviewerId,
    );
    await createReview.click();
    await expect(page.getByText(/复核已创建/)).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "领取复核" }).click();
    await expect(page.getByText(/复核动作 claim 已提交/)).toBeVisible({
      timeout: 30_000,
    });
    await page
      .getByRole("checkbox", {
        name: /我已核对 Schema 快照、标签完整性、证据与职责分离/,
      })
      .check();
    await page.getByRole("button", { name: /^通\s*过$/ }).click();
    await expect(page.getByText(/复核动作 approve 已提交/)).toBeVisible({
      timeout: 30_000,
    });

    // 6. 复核后身份切回标注人，界面显示服务端推进后的终态。
    await page.setExtraHTTPHeaders({ "X-User-Id": userId });
    await page.reload();
    await expect(page.getByText("复核：已通过")).toBeVisible({ timeout: 30_000 });
  });
});

test.describe("关键路径：标注控制台播放控制（V31-045 切片 A）", () => {
  skipWithoutSeed(
    ["DDP_E2E_ANNOTATION_ASSIGNMENT_ID", "DDP_E2E_ANNOTATION_TASK_ID"],
    "播放控制需要真实栈标注链路种子",
  );

  test("工作台媒体面板提供倍速/帧步进/时钟，键盘逐帧生效", async ({
    page,
  }) => {
    const assignmentId = read("DDP_E2E_ANNOTATION_ASSIGNMENT_ID");
    const projectId = read("DDP_E2E_PROJECT_ID");
    await page.goto(
      `/annotation/workbench/${assignmentId}?project_id=${projectId}`,
    );
    const video = page.getByTestId("media-preview-video");
    await expect(video).toBeVisible({ timeout: 30_000 });

    // 倍速：点击 0.5x 后 video.playbackRate 生效
    await page.getByTestId("media-rate-0.5").click();
    await expect
      .poll(() => video.evaluate((el) => el.playbackRate), { timeout: 5_000 })
      .toBe(0.5);
    await page.getByTestId("media-rate-1").click();
    await expect
      .poll(() => video.evaluate((el) => el.playbackRate), { timeout: 5_000 })
      .toBe(1);

    // 帧步进按钮：currentTime 以 1/25s 移动
    const before = await video.evaluate((el) => el.currentTime);
    await page.getByText("帧 ›").click();
    const after = await video.evaluate((el) => el.currentTime);
    expect(after - before).toBeCloseTo(1 / 25, 3);

    // 快捷键：句点逐帧、右箭头跳 1s
    await video.focus();
    await page.keyboard.press(".");
    const frameStepped = await video.evaluate((el) => el.currentTime);
    expect(frameStepped - after).toBeCloseTo(1 / 25, 3);
    await page.keyboard.press("ArrowRight");
    const arrowStepped = await video.evaluate((el) => el.currentTime);
    expect(arrowStepped - frameStepped).toBeCloseTo(1, 2);

    // 时钟显示
    await expect(page.getByTestId("media-clock")).toBeVisible();
  });
});
