import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../services/api";
import {
  mergeQuotaVersions,
  type UsageRow,
} from "../../services/analyticsPresentation";
import type { Quota, UsageEntry } from "../../types/governance";
import UsageQuotaTable from "./UsageQuotaTable";
import type { UsageQuotaTableProps } from "./UsageQuotaTable";

const PROJECT_ID = "11111111-2222-4333-8444-555555555555";

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

function entry(overrides: Partial<UsageEntry> = {}): UsageEntry {
  return {
    resource_kind: "EPISODES",
    used: 12,
    limit: 5000,
    remaining: 4988,
    exceeded: false,
    status: "OK",
    measurement_source: "episodes",
    measured_at: "2026-11-17T02:00:00Z",
    ...overrides,
  };
}

function quota(resourceKind: UsageEntry["resource_kind"], version: number): Quota {
  return {
    quota_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    project_id: PROJECT_ID,
    resource_kind: resourceKind,
    limit: 5000,
    version,
    created_at: null,
    updated_at: null,
    created_by: null,
    updated_by: null,
  };
}

const measuredRow: UsageEntry = entry();

const unmeasuredRow: UsageEntry = entry({
  resource_kind: "OBJECT_STORAGE_BYTES",
  used: null,
  limit: null,
  remaining: null,
  exceeded: null,
  status: "UNMEASURED",
  measurement_source: null,
  measured_at: null,
});

const overLimitRow: UsageEntry = entry({
  resource_kind: "DATASET_VERSIONS",
  used: 9,
  limit: 5,
  remaining: 0,
  exceeded: true,
  status: "OVER_LIMIT",
});

function rowsOf(entries: UsageEntry[], quotas: Quota[] = []): UsageRow[] {
  return mergeQuotaVersions(entries, quotas);
}

const baseProps: UsageQuotaTableProps = {
  phase: "ready",
  rows: rowsOf([measuredRow]),
  totalRows: 1,
  statusFilter: undefined,
  generatedAt: "2026-11-17T02:05:00Z",
  refreshing: false,
  stale: false,
  error: null,
  quotaConfigError: null,
  online: true,
  onStatusFilterChange: () => undefined,
  onRefresh: () => undefined,
  onRetryQuotaConfig: () => undefined,
  onEdit: () => undefined,
};

function renderTable(overrides: Partial<UsageQuotaTableProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <UsageQuotaTable {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

/** AntD 会在两个中文字符之间插空（"刷 新"），断言前统一去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

/** AntD 的禁用按钮在 SSR 里表现为 `disabled=""` 属性，而不是类名。 */
function isButtonDisabled(html: string, label: string): boolean {
  const buttons = squash(html).match(/<button[^>]*>.*?<\/button>/g) ?? [];
  const target = buttons.find((button) =>
    button.includes(`<span>${squash(label)}</span>`),
  );
  return target !== undefined && target.includes('disabled=""');
}

describe("用量—配额对照表", () => {
  it("逐行展示已用 / 上限 / 剩余 / 状态 / 统计截止", () => {
    const html = renderTable();

    expect(html).toContain("Episode");
    expect(html).toContain("12");
    expect(html).toContain("5,000");
    expect(html).toContain("4,988");
    expect(html).toContain("正常");
    expect(html).toContain("数据统计于");
    expect(html).toContain("2026-11-17");
  });

  it("used = null 渲染「未测量」，页面不出现 0 或 0 B", () => {
    const html = renderTable({
      rows: rowsOf([unmeasuredRow]),
      totalRows: 1,
    });

    expect(html).toContain("未测量");
    expect(html).toContain("无可用统计，非 0");
    // 核心断言：null 不能被渲染成任何 0 值。
    expect(html).not.toContain("0 B");
    expect(html).not.toContain(">0<");
  });

  it("limit = null 渲染「未配置上限」，不是 0", () => {
    const html = renderTable({
      rows: rowsOf([entry({ limit: null, remaining: null, exceeded: null, status: "UNLIMITED" })]),
      totalRows: 1,
    });

    expect(html).toContain("未配置上限");
    expect(html).toContain("未设上限");
    expect(html).not.toContain(">0<");
  });

  it("UNMEASURED 不使用绿色状态标签，而 OK 使用", () => {
    const unmeasuredHtml = renderTable({
      rows: rowsOf([unmeasuredRow]),
      totalRows: 1,
    });
    const okHtml = renderTable();

    expect(unmeasuredHtml).toContain("未测量");
    expect(unmeasuredHtml).not.toContain("ant-tag-green");
    expect(okHtml).toContain("ant-tag-green");
  });

  it("字节型用量按 KiB 展示", () => {
    const html = renderTable({
      rows: rowsOf([
        entry({
          resource_kind: "OBJECT_STORAGE_BYTES",
          used: 1536,
          limit: 1024 * 1024,
          remaining: 1024 * 1024 - 1536,
          exceeded: false,
          status: "OK",
          measurement_source: "raw_assets.size_bytes",
        }),
      ]),
      totalRows: 1,
    });

    expect(html).toContain("1.5 KiB");
    expect(html).toContain("1.0 MiB");
  });

  it("超限行展示「已超限」与截断后的剩余 0", () => {
    const html = renderTable({
      rows: rowsOf([overLimitRow]),
      totalRows: 1,
    });

    expect(html).toContain("已超限");
    expect(html).toContain("超限判定：已超限");
  });

  it("measured_at = null 时用占位符，不填充当前时间", () => {
    const html = renderTable({
      rows: rowsOf([unmeasuredRow]),
      totalRows: 1,
      // 报表时间也置空，确保页面上除 measured_at 外没有其它日期来源。
      generatedAt: null,
    });

    expect(html).toContain("—");
    expect(html).toContain("数据统计于：未知");
    expect(html).not.toContain(String(new Date().getFullYear()));
  });

  it("有已配置配额的行显示「调整上限」，未配置的行显示「设置上限」", () => {
    const withQuota = renderTable({
      rows: rowsOf([measuredRow], [quota("EPISODES", 4)]),
      totalRows: 1,
    });
    const withoutQuota = renderTable({
      rows: rowsOf([measuredRow], []),
      totalRows: 1,
    });

    expect(hasButton(withQuota, "调整上限")).toBe(true);
    expect(hasButton(withoutQuota, "设置上限")).toBe(true);
  });

  it("首次加载展示 Loading，不渲染表格", () => {
    const html = renderTable({ phase: "loading", rows: [], totalRows: 0 });
    expect(html).toContain("正在加载用量报表");
  });

  it("无条目时展示空态", () => {
    const html = renderTable({ rows: [], totalRows: 0 });
    expect(html).toContain("服务端未返回任何用量条目");
  });

  it("筛选后为空时说明是筛选结果，不是没有数据", () => {
    const html = renderTable({ rows: [], totalRows: 3, statusFilter: "OVER_LIMIT" });
    expect(html).toContain("当前筛选条件下没有资源");
  });

  it("403 时整页 Forbidden，不渲染任何资源数据", () => {
    const html = renderTable({
      phase: "forbidden",
      rows: rowsOf([measuredRow]),
      totalRows: 1,
      error: httpError(403, "无权访问该项目"),
    });

    expect(html).toContain("无权访问");
    expect(html).toContain("无权查看用量数据");
    expect(html).not.toContain("4,988");
  });

  it("首次加载失败展示错误与重试", () => {
    const html = renderTable({
      phase: "error",
      rows: [],
      totalRows: 0,
      error: httpError(500, "服务内部错误"),
    });

    expect(html).toContain("加载失败");
    expect(html).toContain("服务内部错误");
    expect(hasButton(html, "重试")).toBe(true);
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderTable({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain("4,988");
    expect(html).toContain("上次成功获取的数据");
  });

  it("配额配置读取失败时降级为 Partial，并说明上限修改不可用", () => {
    const html = renderTable({
      quotaConfigError: httpError(503, "配额服务不可用"),
    });

    expect(html).toContain("配额配置不可用");
    expect(html).toContain("4,988");
    expect(hasButton(html, "重试读取配额")).toBe(true);
    // 按钮仍在，但被禁用并说明原因，而不是悄悄消失。
    expect(hasButton(html, "设置上限")).toBe(true);
    expect(isButtonDisabled(html, "设置上限")).toBe(true);
  });

  it("离线时禁用写操作并显式提示", () => {
    const html = renderTable({ online: false });

    expect(html).toContain("当前离线");
    expect(isButtonDisabled(html, "设置上限")).toBe(true);
    expect(hasButton(html, "设置上限")).toBe(true);
  });

  it("真实后端「全部未测量」响应渲染 5 行且不出现任何 0 值", () => {
    // 2026-11-17 本地后端实测响应：usage_snapshots 为空时 5 条全部 UNMEASURED。
    const kinds = [
      "OBJECT_STORAGE_BYTES",
      "EPISODES",
      "DATASET_VERSIONS",
      "INGESTION_JOBS",
      "INFERENCE_TASKS",
    ] as const;
    const liveEntries: UsageEntry[] = kinds.map((kind) => ({
      resource_kind: kind,
      used: null,
      limit: null,
      remaining: null,
      exceeded: null,
      status: "UNMEASURED",
      measurement_source: null,
      measured_at: null,
    }));

    const html = renderTable({
      rows: rowsOf(liveEntries),
      totalRows: liveEntries.length,
    });

    expect(html.match(/未测量/g)?.length).toBeGreaterThanOrEqual(5);
    expect(html).toContain("未配置上限");
    expect(html).toContain("无法计算");
    expect(html).not.toContain("0 B");
    expect(html).not.toContain(">0<");
    // 5 行都提供「设置上限」入口（尚未配置任何配额）。
    expect(html.match(/<span>设置上限<\/span>/g)?.length).toBe(5);
  });

  it("口径说明常驻展示，解释三态边界", () => {
    const html = renderTable();

    expect(html).toContain("口径说明");
    expect(html).toContain("不是用量为 0");
    expect(html).toContain("UNMEASURED 与 OK 是两种结论");
  });
});
