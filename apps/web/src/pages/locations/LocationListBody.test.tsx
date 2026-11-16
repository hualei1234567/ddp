import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  LOCATION_ID,
  locationFixture,
} from "../../services/__fixtures__/collectionAssetFixtures";
import { ApiError } from "../../services/api";
import LocationListBody from "./LocationListBody";
import type { LocationListBodyProps } from "./LocationListBody";

/** AntD 会在两个中文字符的按钮文本间插入空格（"重 试"），断言前先去空白。 */
function squash(html: string): string {
  return html.replace(/\s+/g, "");
}

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

const baseProps: LocationListBodyProps = {
  kind: undefined,
  phase: "ready",
  items: [locationFixture()],
  loading: false,
  refreshing: false,
  loadingMore: false,
  hasMore: false,
  error: null,
  loadMoreError: null,
  stale: false,
  onKindChange: () => undefined,
  onReload: () => undefined,
  onLoadMore: () => undefined,
  onCreate: () => undefined,
};

function renderList(overrides: Partial<LocationListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <LocationListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("场地列表", () => {
  it("展示名称、编码、类型与地址", () => {
    const html = renderList();

    expect(html).toContain("上海一号基地");
    expect(html).toContain("SITE-001");
    expect(html).toContain("基地");
    expect(html).toContain("上海市浦东新区");
    expect(html).toContain(`/locations/${LOCATION_ID}`);
  });

  it("没有删除按钮，也没有自造的状态列", () => {
    const html = renderList();

    expect(html).not.toContain("ant-btn-dangerous");
    expect(html).not.toContain(">状态<");
  });

  it("无数据时展示空态", () => {
    const html = renderList({ items: [], phase: "ready" });
    expect(html).toContain("暂无场地");
  });

  it("403 时整页 Forbidden，不渲染场地数据", () => {
    const html = renderList({
      phase: "forbidden",
      error: httpError(403, "无权访问该项目"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain("上海一号基地");
  });

  it("刷新失败保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain("上海一号基地");
    expect(html).toContain("上次成功获取的数据");
  });

  it("首次加载失败展示错误与重试", () => {
    const html = renderList({
      phase: "error",
      items: [],
      error: httpError(500, "服务内部错误"),
    });

    expect(html).toContain("服务内部错误");
    expect(squash(html)).toContain("重试");
  });

  it("缺失地址显示占位符而不是 undefined", () => {
    const html = renderList({
      items: [locationFixture({ address: null })],
    });

    // 地址单元格必须是占位符，不能把 null 渲染成空白或 undefined。
    expect(html).toContain('<td class="ant-table-cell">—</td>');
  });
});
