import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  planFixture,
  scenarioFixture,
  sopFixture,
} from "../../services/__fixtures__/collectionAssetFixtures";
import { ApiError } from "../../services/api";
import AssetListBody from "./AssetListBody";
import type { AssetListBodyProps } from "./AssetListBody";

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

/** AntD 会在两个中文字符的按钮文本间插入空格（"激 活"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

/** 只匹配按钮文本节点，避免把说明文案里的同名词算成按钮。 */
function hasButton(html: string, label: string): boolean {
  return squash(html).includes(`<span>${squash(label)}</span>`);
}

const baseProps: AssetListBodyProps = {
  kind: "plans",
  status: undefined,
  phase: "ready",
  items: [planFixture()],
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
  busyAssetId: null,
  onStatusChange: () => undefined,
  onReload: () => undefined,
  onLoadMore: () => undefined,
  onCreate: () => undefined,
  onAction: () => Promise.resolve(),
};

function renderList(overrides: Partial<AssetListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <AssetListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("采集素材列表", () => {
  it("DRAFT 行只有服务端给的 activate 按钮", () => {
    const html = renderList();

    expect(html).toContain("仓储拣选采集方案");
    expect(html).toContain("PLAN-001");
    expect(html).toContain("草稿");
    expect(html).toContain("方案版本");
    expect(html).toContain("v2");
    expect(hasButton(html, "激活")).toBe(true);
    expect(hasButton(html, "归档")).toBe(false);
  });

  it("ACTIVE 行只有 archive 按钮", () => {
    const html = renderList({
      items: [
        planFixture({ status: "ACTIVE", allowed_actions: ["archive"] }),
      ],
    });

    expect(hasButton(html, "归档")).toBe(true);
    expect(hasButton(html, "激活")).toBe(false);
  });

  it("ARCHIVED 行没有任何动作按钮", () => {
    const html = renderList({
      items: [
        planFixture({ status: "ARCHIVED", allowed_actions: [] }),
      ],
    });

    expect(hasButton(html, "激活")).toBe(false);
    expect(hasButton(html, "归档")).toBe(false);
    expect(html).toContain("无动作");
  });

  it("详情链接指向所属素材类型", () => {
    const html = renderList();
    expect(html).toContain(
      `/collection-assets/plans/${planFixture().plan_id}`,
    );
  });

  it("无数据时展示空态", () => {
    const html = renderList({ items: [], phase: "ready" });
    expect(html).toContain("暂无采集方案");
  });

  it("403 时整页 Forbidden，不渲染素材数据", () => {
    const html = renderList({
      phase: "forbidden",
      items: [planFixture()],
      error: httpError(403, "无权访问该项目"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain("仓储拣选采集方案");
    expect(html).not.toContain(planFixture().plan_id);
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain("仓储拣选采集方案");
    expect(html).toContain("上次成功获取的数据");
  });

  it("首次加载失败展示错误与重试", () => {
    const html = renderList({
      phase: "error",
      items: [],
      error: httpError(500, "服务内部错误"),
    });

    expect(html).toContain("加载失败");
    expect(html).toContain("服务内部错误");
    expect(hasButton(html, "重试")).toBe(true);
  });

  it("加载更多失败展示 Partial，已加载数据继续可用", () => {
    const html = renderList({
      hasMore: true,
      loadMoreError: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain("部分数据不可用");
    expect(html).toContain("仓储拣选采集方案");
  });

  it("SOP 列表展示 SOP 版本而不是方案版本", () => {
    const html = renderList({
      kind: "sops",
      items: [sopFixture()],
    });

    expect(html).toContain("SOP 版本");
    expect(html).toContain("1.0.0");
    expect(html).toContain("上料 SOP");
  });

  it("场景列表展示场景标签", () => {
    const html = renderList({
      kind: "scenarios",
      items: [scenarioFixture()],
    });

    expect(html).toContain("场景标签");
    expect(html).toContain("室内");
    expect(html).toContain("货架");
  });
});
