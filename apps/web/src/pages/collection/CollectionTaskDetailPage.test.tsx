import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  collectionBatchFixture,
  collectionTaskFixture,
  PROJECT_ID,
  TASK_ID,
} from "../../services/__fixtures__/collectionFixtures";
import {
  EMPTY_TASK_LIST_FILTERS,
} from "../../services/collectionUrl";
import BatchListBody from "./taskDetail/BatchListBody";
import type { BatchListBodyProps } from "./taskDetail/BatchListBody";
import CollectionTaskDetailBody from "./taskDetail/CollectionTaskDetailBody";
import type { CollectionTaskDetailBodyProps } from "./taskDetail/CollectionTaskDetailBody";

const emptyBatches: BatchListBodyProps = {
  items: [],
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
  isMobile: false,
  onRetry: () => undefined,
  onLoadMore: () => undefined,
};

const baseProps: CollectionTaskDetailBodyProps = {
  projectId: PROJECT_ID,
  taskId: TASK_ID,
  filters: EMPTY_TASK_LIST_FILTERS,
  data: collectionTaskFixture,
  phase: "ready",
  error: null,
  stale: false,
  activeTab: "overview",
  actions: [],
  busyAction: null,
  feedback: null,
  batchesContent: <BatchListBody {...emptyBatches} />,
  onTabChange: () => undefined,
  onAction: () => undefined,
  onRetry: () => undefined,
  onBackToList: () => undefined,
};

function renderDetail(
  overrides: Partial<CollectionTaskDetailBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <CollectionTaskDetailBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("采集任务详情页面状态", () => {
  it("403 时整页 Forbidden，不展示 SOP、人员与设备信息", () => {
    const html = renderDetail({
      phase: "forbidden",
      error: new ApiError({
        kind: "http",
        status: 403,
        code: "forbidden",
        message: "无权访问该项目",
      }),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(collectionTaskFixture.sop_version ?? "");
    expect(html).not.toContain(collectionTaskFixture.assigned_to ?? "");
  });

  it("404 展示任务不存在空态", () => {
    const html = renderDetail({
      data: null,
      phase: "not-found",
      error: new ApiError({
        kind: "http",
        status: 404,
        code: "resource_not_found",
        message: "不存在",
      }),
    });
    expect(html).toContain("任务不存在或无权查看");
    expect(html).toContain("返回任务列表");
  });

  it("allowed_actions 缺失时禁用写操作并说明原因", () => {
    const html = renderDetail();
    expect(html).toContain("服务端未下发可执行动作");
    expect(html).not.toContain("开始采集</button>");
  });

  it("只渲染服务端下发的已知动作", () => {
    const html = renderDetail({
      actions: [
        { action: "pause", label: "暂停采集", requiresReason: true },
        { action: "submit", label: "提交任务", requiresReason: false },
      ],
    });
    expect(html).toContain("暂停采集");
    expect(html).toContain("提交任务");
    expect(html).toContain("进入现场工作台");
  });

  it("刷新失败保留详情并提示 Stale", () => {
    const html = renderDetail({
      stale: true,
      error: new ApiError({
        kind: "http",
        status: 503,
        code: "dependency_unavailable",
        message: "服务不可用",
      }),
    });
    expect(html).toContain(collectionTaskFixture.name);
    expect(html).toContain("数据可能已过期");
  });

  it("409 反馈提示刷新且不覆盖他人修改", () => {
    const html = renderDetail({
      feedback: {
        kind: "warning",
        title: "任务已被更新或当前状态不允许该动作",
        description: "版本冲突；请刷新后基于最新版本重新操作。",
      },
    });
    expect(html).toContain("任务已被更新");
    expect(html).toContain("刷新任务");
  });

  it("概览展示长 ID 复制入口与 SOP 快照", () => {
    const html = renderDetail();
    expect(html).toContain("22222222…2222");
    expect(html).toContain("复制任务 ID");
    expect(html).toContain(collectionTaskFixture.sop_version ?? "");
  });

  it("批次 Tab 空态、加载失败与过期状态可区分", () => {
    const emptyHtml = renderDetail({ activeTab: "batches" });
    expect(emptyHtml).toContain("暂无批次");

    const errorHtml = renderDetail({
      activeTab: "batches",
      batchesContent: (
        <BatchListBody
          {...emptyBatches}
          error={new ApiError({
            kind: "http",
            status: 500,
            code: "internal_error",
            message: "批次加载失败",
          })}
        />
      ),
    });
    expect(errorHtml).toContain("批次加载失败");
    expect(errorHtml).toContain("internal_error");

    const staleHtml = renderDetail({
      activeTab: "batches",
      batchesContent: (
        <BatchListBody
          {...emptyBatches}
          items={[collectionBatchFixture]}
          stale
        />
      ),
    });
    expect(staleHtml).toContain("上次成功获取的批次数据");
  });

  it("操作记录 Tab 明确 Disabled，不伪造审计时间线", () => {
    const html = renderDetail({ activeTab: "operations" });
    expect(html).toContain("操作记录接口尚未定义");
  });
});
