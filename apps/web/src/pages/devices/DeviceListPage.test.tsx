import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { backendDeviceFixture } from "../../services/__fixtures__/deviceContract.fixtures";
import { ApiError } from "../../services/api";
import { EMPTY_DEVICE_LIST_FILTERS } from "../../services/deviceListUrl";
import type { Device } from "../../types/device";
import { DeviceListBody } from "./DeviceListPage";
import type { DeviceListBodyProps } from "./DeviceListPage";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const NOW = Date.parse("2026-11-08T12:00:00Z");
const device: Device = { ...backendDeviceFixture };

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

const baseProps: DeviceListBodyProps = {
  projectId: PROJECT_ID,
  filters: EMPTY_DEVICE_LIST_FILTERS,
  phase: "ready",
  error: null,
  items: [device],
  filteredItems: [device],
  hasMore: false,
  loading: false,
  refreshing: false,
  loadingMore: false,
  loadMoreError: null,
  stale: false,
  updatedAt: NOW,
  isMobile: false,
  onRetry: () => undefined,
  onResetFilters: () => undefined,
  onLoadMore: () => undefined,
  onOpenDevice: () => undefined,
  now: NOW,
};

function renderList(overrides: Partial<DeviceListBodyProps> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <DeviceListBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("设备列表页面状态", () => {
  it("403 时整页 Forbidden，不渲染设备数据", () => {
    const html = renderList({
      phase: "forbidden",
      error: httpError(403, "无权访问该项目"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(device.name);
    expect(html).not.toContain(device.device_id);
  });

  it("首次加载成功但无设备时展示空态", () => {
    const html = renderList({
      items: [],
      filteredItems: [],
      hasMore: false,
    });

    expect(html).toContain("尚未登记设备");
    expect(html).toContain("当前项目还没有设备记录");
  });

  it("有设备但筛选无匹配时展示无匹配空态", () => {
    const html = renderList({
      filters: { ...EMPTY_DEVICE_LIST_FILTERS, keyword: "不存在" },
      filteredItems: [],
    });

    expect(html).toContain("无匹配结果");
    expect(html).toContain("清空筛选");
  });

  it("刷新失败时保留旧数据并提示 Stale", () => {
    const html = renderList({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain(device.name);
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的数据");
  });

  it("心跳超时进入 Stale，且 ACTIVE 不再显示为可用", () => {
    const staleDevice: Device = {
      ...device,
      status: "ACTIVE",
      last_heartbeat_at: "2026-11-08T11:00:00Z",
      heartbeat_timeout_seconds: 60,
    };
    const html = renderList({ items: [staleDevice], filteredItems: [staleDevice] });

    expect(html).toContain("部分设备状态可能已过期");
    expect(html).toContain("心跳超时 1 台");
    expect(html).toContain("状态可能过期");
  });

  it("详情链接携带项目与筛选条件，长 ID 提供复制入口", () => {
    const html = renderList({
      filters: { ...EMPTY_DEVICE_LIST_FILTERS, keyword: "SN-001" },
    });

    expect(html).toContain(
      `/devices/${device.device_id}?project_id=${PROJECT_ID}&amp;q=SN-001`,
    );
    expect(html).toContain("22222222…2222");
    expect(html).toContain("复制设备 ID");
  });

  it("窄屏使用卡片而不是横向表格", () => {
    const html = renderList({ isMobile: true });

    expect(html).toContain(device.name);
    expect(html).toContain("设备 ID");
    expect(html).toContain("ant-card");
    expect(html).not.toContain("ant-table");
  });
});
