import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { auditFixture } from "../../services/__fixtures__/auditLogFixtures";
import { ApiError } from "../../services/api";
import AuditLogListBody from "./AuditLogListBody";
import type { AuditLogListBodyProps } from "./AuditLogListBody";

function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

const baseProps: AuditLogListBodyProps = {
  filters: {},
  phase: "ready",
  items: [auditFixture()],
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
  selected: null,
  onFiltersChange: () => undefined,
  onClearFilters: () => undefined,
  onReload: () => undefined,
  onLoadMore: () => undefined,
  onSelect: () => undefined,
};

function renderList(overrides: Partial<AuditLogListBodyProps> = {}): string {
  return renderToStaticMarkup(<AuditLogListBody {...baseProps} {...overrides} />);
}

describe("审计日志列表", () => {
  it("展示资源类型、动作、状态迁移、原因与操作者", () => {
    const html = renderList();

    expect(html).toContain("RAW_ASSET");
    expect(html).toContain("validate");
    expect(html).toContain("UPLOADED");
    expect(html).toContain("VALID");
    expect(html).toContain("校验通过");
    expect(html).toContain("owner");
    expect(html).toContain("成功");
  });

  it("不提供任何写入操作", () => {
    const html = renderList();

    expect(html).not.toContain("新建");
    expect(html).not.toContain("删除");
    expect(html).not.toContain("编辑");
  });

  it("过滤区覆盖资源类型、动作、结果与时间区间", () => {
    const html = renderList();

    expect(html).toContain("资源类型，如 RAW_ASSET");
    expect(html).toContain("动作，如 create");
    expect(html).toContain("全部结果");
    expect(html).toContain("开始日期");
    expect(html).toContain("结束日期");
    expect(html).toContain("清空筛选");
  });

  it("无数据时展示空态", () => {
    const html = renderList({ items: [], phase: "ready" });
    expect(html).toContain("暂无审计记录");
  });

  it("加载失败时展示错误态", () => {
    const html = renderList({
      items: [],
      phase: "error",
      error: httpError(500, "服务暂时不可用"),
    });

    expect(html).toContain("加载失败");
    expect(html).toContain("服务暂时不可用");
  });

  it("403 时整页 Forbidden，不渲染审计数据", () => {
    const html = renderList({
      phase: "forbidden",
      error: httpError(403, "该动作要求项目负责人权限"),
    });

    expect(squash(html)).toContain("无权访问");
    // 占位符里也有 RAW_ASSET 字样，因此断言业务数据本身没有渲染。
    expect(html).not.toContain("校验通过");
    expect(html).not.toContain("UPLOADED");
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(500, "刷新失败"),
    });

    expect(html).toContain("上次成功获取的数据");
    expect(html).toContain("校验通过");
  });

  it("还有下一页时展示加载更多", () => {
    const html = renderList({ hasMore: true });
    expect(html).toContain("加载更多");
  });
});
