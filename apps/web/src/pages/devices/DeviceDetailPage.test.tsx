import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { backendDeviceFixture } from "../../services/__fixtures__/deviceContract.fixtures";
import { ApiError } from "../../services/api";
import type { Device } from "../../types/device";
import { DeviceDetailBody } from "./DeviceDetailPage";
import type { DeviceDetailBodyProps } from "./DeviceDetailPage";

const PROJECT_ID = "11111111-1111-7111-8111-111111111111";
const NOW = Date.parse("2026-11-08T12:00:00Z");
const device: Device = { ...backendDeviceFixture };

function httpError(status: number, message: string): ApiError {
  return new ApiError({ kind: "http", status, message, code: `http_${status}` });
}

const baseProps: DeviceDetailBodyProps = {
  projectId: PROJECT_ID,
  deviceId: device.device_id,
  data: device,
  phase: "ready",
  error: null,
  stale: false,
  activeTab: "overview",
  listSearch: `?project_id=${PROJECT_ID}`,
  tabsRefreshToken: 0,
  onTabChange: () => undefined,
  onRetry: () => undefined,
  onBackToList: () => undefined,
  now: NOW,
};

function renderDetail(
  overrides: Partial<DeviceDetailBodyProps> = {},
): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <DeviceDetailBody {...baseProps} {...overrides} />
    </MemoryRouter>,
  );
}

describe("设备详情页面状态", () => {
  it("403 时整页 Forbidden，不展示缓存档案", () => {
    const html = renderDetail({
      phase: "forbidden",
      error: httpError(403, "无权访问该设备"),
    });

    expect(html).toContain("无权访问");
    expect(html).not.toContain(device.name);
    expect(html).not.toContain(device.serial_number ?? "");
  });

  it("404 时展示设备不存在空态并返回列表", () => {
    const html = renderDetail({
      data: null,
      phase: "not-found",
      error: httpError(404, "设备不存在"),
    });

    expect(html).toContain("设备不存在或无权查看");
    expect(html).toContain("返回设备列表");
  });

  it("缺少详情数据时展示空态", () => {
    const html = renderDetail({ data: null });

    expect(html).toContain("暂无设备详情");
  });

  it("刷新失败时保留旧档案并提示 Stale", () => {
    const html = renderDetail({
      stale: true,
      error: httpError(503, "服务暂时不可用"),
    });

    expect(html).toContain(device.name);
    expect(html).toContain("数据可能已过期");
    expect(html).toContain("上次成功获取的数据");
  });

  it("心跳超时进入 Stale 并展示最近心跳", () => {
    const staleDevice: Device = {
      ...device,
      status: "ACTIVE",
      last_heartbeat_at: "2026-11-08T11:00:00Z",
      heartbeat_timeout_seconds: 60,
    };
    const html = renderDetail({ data: staleDevice });

    expect(html).toContain("设备状态可能已过期");
    expect(html).toContain("最近心跳");
    expect(html).toContain("状态可能过期");
  });

  it("设备 ID 提供完整查看与复制入口", () => {
    const html = renderDetail();

    expect(html).toContain("22222222…2222");
    expect(html).toContain("复制设备 ID");
  });
});
