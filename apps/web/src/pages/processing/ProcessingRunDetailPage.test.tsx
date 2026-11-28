import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import type { ProcessingRun, ProcessingRunStep } from "../../types/processing";
import {
  ProcessingRunDetailBody,
  type ProcessingRunDetailBodyProps,
} from "./ProcessingRunDetailPage";

/** AntD 会在中文与计数之间插入空白，断言前先去掉空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function stepFixture(overrides: Partial<ProcessingRunStep> = {}): ProcessingRunStep {
  return {
    step_id: "2a0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a02",
    run_id: "1f0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a01",
    stage: "FILE_VALIDATION",
    attempt: 1,
    status: "SUCCEEDED",
    log_summary: "校验通过",
    error_code: null,
    error_summary: null,
    started_at: "2026-11-27T10:00:00Z",
    finished_at: "2026-11-27T10:00:20Z",
    created_at: "2026-11-27T10:00:00Z",
    updated_at: "2026-11-27T10:00:20Z",
    ...overrides,
  };
}

function runFixture(overrides: Partial<ProcessingRun> = {}): ProcessingRun {
  return {
    run_id: "1f0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a01",
    project_id: "0d7a2f31-9c1b-4f7e-8e5a-2b3c4d5e6f70",
    pipeline_version: "v1.2.0",
    input_refs: ["raw-assets/abc.parquet"],
    output_refs: [],
    status: "FAILED",
    stage: "EPISODE_SEGMENTATION",
    attempt: 2,
    retry_count: 1,
    error_code: "E_SEGMENT_FAILED",
    error_summary: "切分阶段失败",
    started_at: "2026-11-27T10:00:00Z",
    finished_at: "2026-11-27T10:05:30Z",
    closed_at: null,
    allowed_actions: ["cancel"],
    version: 5,
    created_at: "2026-11-27T09:59:00Z",
    updated_at: "2026-11-27T10:05:31Z",
    steps: [
      stepFixture(),
      stepFixture({
        step_id: "3a0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a03",
        stage: "EPISODE_SEGMENTATION",
        attempt: 2,
        status: "FAILED",
        log_summary: null,
        error_code: "E_SEGMENT_FAILED",
        error_summary: "切分阶段失败",
        finished_at: "2026-11-27T10:05:30Z",
      }),
    ],
    ...overrides,
  };
}

const baseProps: ProcessingRunDetailBodyProps = {
  data: null,
  loading: false,
  error: null,
  stale: false,
  onRetry: () => undefined,
  onAct: async () => undefined,
  busy: false,
};

/**
 * 直接渲染**真实组件**（不是复刻它的分支逻辑）。
 *
 * 为什么强调这一点：早先版本在测试里自己拼了一份 HTML（"等价复刻"），
 * 结果测试断言的是测试自己的副本——真实组件展示「消息 + 错误码 +
 * request_id」而复刻版只有消息，断言必然对不上。钉住契约只能靠
 * 渲染被测组件本身。
 */
function renderBody(overrides: Partial<ProcessingRunDetailBodyProps> = {}): string {
  return squash(
    renderToStaticMarkup(
      <MemoryRouter>
        <ProcessingRunDetailBody {...baseProps} {...overrides} />
      </MemoryRouter>,
    ),
  );
}

describe("处理运行详情四态与数据渲染", () => {
  it("数据态：渲染运行标识、流水线版本、当前阶段与错误信息", () => {
    const html = renderBody({ data: runFixture() });

    expect(html).toContain("1f0e5322-6a1a-4b1e-9f5a-3c2b1d0e9a01");
    expect(html).toContain("v1.2.0");
    expect(html).toContain("E_SEGMENT_FAILED");
    expect(html).toContain("切分阶段失败");
  });

  it("数据态：渲染阶段步骤表（阶段 / 状态 / 尝试）", () => {
    const html = renderBody({ data: runFixture() });

    // 阶段经 presentStage 翻译为中文展示名（与列表页同一口径）。
    expect(html).toContain("文件校验");
    expect(html).toContain("Episode切分");
    expect(html).toContain("校验通过");
  });

  it("数据态：动作按钮只按服务端 allowed_actions 渲染", () => {
    const withCancel = renderBody({ data: runFixture({ allowed_actions: ["cancel"] }) });
    const withoutActions = renderBody({ data: runFixture({ allowed_actions: [] }) });

    expect(withCancel).toContain("取消");
    expect(withoutActions).not.toContain("action=cancel");
  });

  it("Loading 态不出业务字段", () => {
    const html = renderBody({ loading: true });

    expect(html).toContain("正在加载处理运行");
    expect(html).not.toContain("v1.2.0");
  });

  it("Error 态保留错误码与 request_id 并支持重试", () => {
    const error = new ApiError({
      kind: "http",
      status: 500,
      message: "服务暂时不可用",
      code: "http_500",
      requestId: "req-9",
    });
    const html = renderBody({ error });

    expect(html).toContain("服务暂时不可用");
    expect(html).toContain("http_500");
    expect(html).toContain("req-9");
  });

  it("Forbidden 态优先于数据渲染", () => {
    const error = new ApiError({
      kind: "http",
      status: 403,
      message: "无权访问该项目",
      code: "forbidden",
      requestId: "req-403",
    });
    const html = renderBody({ error, data: runFixture() });

    expect(html).toContain("无权访问该项目");
    expect(html).not.toContain("v1.2.0");
  });

  it("Empty 态：既无数据也无错误时给出说明", () => {
    const html = renderBody({ data: null });

    expect(html).toContain("未找到该处理运行");
  });

  it("Stale 态：保留旧数据并提示刷新失败", () => {
    const html = renderBody({ data: runFixture(), stale: true });

    expect(html).toContain("最近一次刷新失败");
    expect(html).toContain("v1.2.0");
  });
});
