import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { UseUploadQueueResult } from "../../hooks/useUploadQueue";
import { ApiError } from "../../services/api";
import {
  collectionTaskFixture,
  PROJECT_ID,
  TASK_ID,
} from "../../services/__fixtures__/collectionFixtures";
import { EMPTY_TASK_LIST_FILTERS } from "../../services/collectionUrl";
import { resolveServerActions } from "../../services/collectionPresentation";
import {
  createUploadQueueItem,
  type UploadQueueItem,
} from "../../services/uploadQueue";
import {
  createEmptyCheckResults,
  SELF_CHECK_ITEMS,
  WORKBENCH_CHECK_ITEMS,
  type CheckResults,
} from "../../services/workbenchChecklist";
import WorkbenchBody from "./workbench/WorkbenchBody";
import type { WorkbenchBodyProps } from "./workbench/WorkbenchBody";

function queueItem(overrides: Partial<UploadQueueItem> = {}): UploadQueueItem {
  return {
    ...createUploadQueueItem({
      id: "item-1",
      fileName: "episode-001.mcap",
      sizeBytes: 100,
      idempotencyKey: "idem-1",
      now: 1000,
    }),
    ...overrides,
  };
}

const uploadQueue: UseUploadQueueResult = {
  items: [],
  addFiles: () => undefined,
  pause: () => undefined,
  resume: () => undefined,
  retry: () => undefined,
  cancel: () => undefined,
  refreshVerification: () => undefined,
  remove: () => undefined,
};

const pendingChecks = createEmptyCheckResults(WORKBENCH_CHECK_ITEMS);
const passedChecks: CheckResults = Object.fromEntries(
  WORKBENCH_CHECK_ITEMS.map((item) => [item.key, "pass"]),
);
const passedSelfChecks: CheckResults = Object.fromEntries(
  SELF_CHECK_ITEMS.map((item) => [item.key, "pass"]),
);

const baseProps: WorkbenchBodyProps = {
  projectId: PROJECT_ID,
  taskId: TASK_ID,
  filters: EMPTY_TASK_LIST_FILTERS,
  data: collectionTaskFixture,
  phase: "ready",
  error: null,
  stale: false,
  step: "check",
  online: true,
  actions: resolveServerActions(["start", "pause", "submit"]),
  checklistResults: pendingChecks,
  selfCheckResults: createEmptyCheckResults(SELF_CHECK_ITEMS),
  uploadQueue,
  busyAction: null,
  feedback: null,
  isMobile: false,
  onStepChange: () => undefined,
  onCheckChange: () => undefined,
  onRecheck: () => undefined,
  onSelfCheckChange: () => undefined,
  onAction: () => undefined,
  onRetry: () => undefined,
  onBackToList: () => undefined,
  now: 2000,
};

function renderWorkbench(
  overrides: Partial<WorkbenchBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <WorkbenchBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("现场工作台状态", () => {
  it("403 时整页 Forbidden，不展示任务与 SOP", () => {
    const html = renderWorkbench({
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权进入",
      }),
    });
    expect(html).toContain("无权访问");
    expect(html).not.toContain(collectionTaskFixture.name);
    expect(html).not.toContain(collectionTaskFixture.sop_version ?? "");
  });

  it("阻塞项未通过时开始采集禁用并说明原因", () => {
    const html = renderWorkbench();
    expect(html).toContain("存在未通过或未确认的阻塞项");
    expect(html).toContain("还有未通过的阻塞项");
    expect(html).toContain("开始采集");
  });

  it("检查全部通过且服务端下发 start 后可开始采集", () => {
    const html = renderWorkbench({ checklistResults: passedChecks });
    expect(html).toContain("所有阻塞项已通过");
    expect(html).not.toContain("还有未通过的阻塞项");
  });

  it("服务端未下发 start 时不根据状态推断动作", () => {
    const html = renderWorkbench({
      actions: resolveServerActions(["pause"]),
      checklistResults: passedChecks,
    });
    expect(html).toContain("服务端未下发 start 动作");
  });

  it("离线时提示并禁止开始与提交", () => {
    const offlineHtml = renderWorkbench({
      online: false,
      checklistResults: passedChecks,
    });
    expect(offlineHtml).toContain("网络离线");
    expect(offlineHtml).toContain("离线时不允许开始新任务");

    const submitHtml = renderWorkbench({
      step: "submit",
      online: false,
      selfCheckResults: passedSelfChecks,
    });
    expect(submitHtml).toContain("待联网提交");
    expect(submitHtml).toContain("离线时不允许提交");
  });

  it("上传步骤区分上传进度、服务端校验与失败重试", () => {
    const uploading = queueItem({
      status: "uploading",
      uploadedBytes: 50,
      startedAt: 1000,
      totalParts: 1,
      uploadedPartCount: 0,
    });
    const verifying = queueItem({
      id: "item-2",
      status: "verifying",
      fileName: "episode-002.mcap",
      uploadedBytes: 100,
      verificationStatus: "PENDING",
    });
    const failed = queueItem({
      id: "item-3",
      status: "failed",
      fileName: "episode-003.mcap",
      error: {
        code: "upload_incomplete",
        summary: "仍有 1 个分片未确认。",
        hint: "仅重试缺失分片：2。",
        retryable: true,
        requiresRefresh: true,
        requiresNewSession: false,
        forbidden: false,
        cancelled: false,
        missingPartNumbers: [2],
      },
    });
    const html = renderWorkbench({
      step: "upload",
      uploadQueue: { ...uploadQueue, items: [uploading, verifying, failed] },
    });

    expect(html).toContain("上传完成");
    expect(html).toContain("已提交，等待服务端校验");
    expect(html).toContain("upload_incomplete");
    expect(html).toContain("重试失败分片");
    expect(html).toContain("缺失分片：2");
  });

  it("返工要求置顶展示，并保留最新状态提示", () => {
    const reworkTask = {
      ...collectionTaskFixture,
      status: "REWORK" as const,
      status_reason: "第 2 批相机遮挡，需重新采集",
    };
    const html = renderWorkbench({
      data: reworkTask,
      step: "submit",
      feedback: {
        kind: "warning",
        title: "任务已被更新或当前状态不允许该动作",
        description: "请刷新后基于最新版本重新操作。",
      },
    });
    expect(html).toContain("返工要求（置顶）");
    expect(html).toContain("第 2 批相机遮挡");
    expect(html).toContain("任务已被更新");
    expect(html).toContain("自检清单尚未全部完成");
  });

  it("移动端工作台展示长 ID 复制入口与纵向步骤", () => {
    const html = renderWorkbench({ isMobile: true, step: "confirm" });
    expect(html).toContain("22222222…2222");
    expect(html).toContain("复制任务 ID");
    expect(html).toContain("任务确认");
  });

  it("任务加载失败展示错误码与重试", () => {
    const html = renderWorkbench({
      phase: "error",
      error: new ApiError({
        kind: "http",
        status: 500,
        code: "internal_error",
        message: "内部错误",
      }),
    });
    expect(html).toContain("加载失败");
    expect(html).toContain("internal_error");
    // antd 对两个汉字的按钮会插空格（重试 → 重 试），
    // 断言前先归一化空白，避免文案渲染细节左右用例。
    expect(html.replace(/\s+/g, "")).toContain("重试");
  });
});
