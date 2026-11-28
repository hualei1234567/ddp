import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import { resolvePagePhase } from "../../services/pagePhase";
import type { ProcessingRun } from "../../types/processing";
import { ProcessingRunListBody } from "./ProcessingRunsPage";
import type { ProcessingRunListBodyProps } from "./ProcessingRunsPage";

/** AntD 会在中文与计数之间插入空白，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function runFixture(overrides: Partial<ProcessingRun> = {}): ProcessingRun {
  return {
    run_id: "1f0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a01",
    project_id: "0d7a2f31-9c1b-4f7e-8e5a-2b3c4d5e6f70",
    pipeline_version: "v1.2.0",
    input_refs: ["raw-assets/abc.parquet"],
    output_refs: ["episodes/seg-001.json"],
    status: "SUCCEEDED",
    stage: "EPISODE_SEGMENTATION",
    attempt: 1,
    retry_count: 0,
    error_code: null,
    error_summary: null,
    started_at: "2026-11-27T10:00:00Z",
    finished_at: "2026-11-27T10:05:30Z",
    closed_at: "2026-11-27T10:05:31Z",
    allowed_actions: [],
    version: 3,
    created_at: "2026-11-27T09:59:00Z",
    updated_at: "2026-11-27T10:05:31Z",
    steps: null,
    ...overrides,
  };
}

const baseProps: ProcessingRunListBodyProps = {
  items: [runFixture()],
  loading: false,
  phase: resolvePagePhase({ hasData: true, loading: false, error: null }),
  error: null,
  hasMore: false,
  loadingMore: false,
  stale: false,
  onRetry: () => undefined,
  onLoadMore: () => undefined,
};

function renderList(overrides: Partial<ProcessingRunListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      renderToStaticMarkup(<ProcessingRunListBody {...baseProps} {...overrides} />)
    </MemoryRouter>
  );
}

describe("处理运行列表四态与数据渲染", () => {
  it("数据态：渲染状态标签、流水线版本、时间与耗时", () => {
    const html = squash(renderList());

    expect(html).toContain("已完成");
    expect(html).toContain("v1.2.0");
    expect(html).toContain("Episode切分");
    expect(html).toContain("2026-11-27");
    expect(html).toContain("5m30s");
  });

  it("数据态：失败运行展示错误码与摘要，成功运行不展示", () => {
    const failed = renderList({
      items: [
        runFixture({
          status: "FAILED",
          stage: "FILE_VALIDATION",
          error_code: "E_FILE_FORMAT",
          error_summary: "不支持的数据文件格式",
        }),
      ],
    });
    expect(squash(failed)).toContain("E_FILE_FORMAT");
    expect(squash(failed)).toContain("不支持的数据文件格式");
    expect(squash(failed)).toContain("失败");

    expect(renderList()).not.toContain("E_FILE_FORMAT");
  });

  it("Loading 态：无数据加载中不出数据行", () => {
    const html = renderList({
      items: [],
      loading: true,
      phase: resolvePagePhase({ hasData: false, loading: true, error: null }),
    });

    expect(html).not.toContain("v1.2.0");
    expect(html).not.toContain("加载失败");
  });

  it("Empty 态：加载完成但无数据展示空态文案", () => {
    const html = renderList({
      items: [],
      phase: resolvePagePhase({ hasData: false, loading: false, error: null }),
    });

    expect(html).toContain("暂无处理运行");
    expect(html).not.toContain("加载失败");
  });

  it("Error 态：保留错误码与 request_id，并提供重试", () => {
    const error = new ApiError({
      kind: "http",
      status: 500,
      message: "服务暂时不可用",
      code: "http_500",
      requestId: "req-123",
    });
    const html = squash(
      renderList({
        items: [],
        phase: resolvePagePhase({ hasData: false, loading: false, error }),
        error,
      }),
    );

    expect(html).toContain("服务暂时不可用");
    expect(html).toContain("http_500");
    expect(html).toContain("req-123");
    expect(html).toContain("重试");
  });

  it("Forbidden 态：优先渲染无权访问，不渲染业务字段", () => {
    const error = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问该项目",
      code: "http_403",
    });
    const html = squash(
      renderList({
        items: [runFixture()],
        phase: resolvePagePhase({ hasData: true, loading: false, error }),
        error,
      }),
    );

    expect(html).toContain("无权访问");
    expect(html).not.toContain("v1.2.0");
    expect(html).not.toContain("1f0e5322");
  });

  it("Stale 态：刷新失败保留旧数据并提示", () => {
    const html = squash(renderList({ stale: true }));

    expect(html).toContain("最近一次刷新失败");
    expect(html).toContain("v1.2.0");
  });

  it("有下一页时提供加载更多", () => {
    const html = squash(renderList({ hasMore: true }));

    expect(html).toContain("加载更多");
    expect(renderList()).not.toContain("加载更多");
  });
});
