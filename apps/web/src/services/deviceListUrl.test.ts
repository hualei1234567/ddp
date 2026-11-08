import { describe, expect, it } from "vitest";

import {
  DEFAULT_DEVICE_DETAIL_TAB,
  EMPTY_DEVICE_LIST_FILTERS,
  buildDeviceSearch,
  hasDeviceListFilters,
  readDeviceListFilters,
  resolveDeviceDetailTab,
} from "./deviceListUrl";

const projectId = "11111111-1111-7111-8111-111111111111";

describe("设备列表 URL 筛选状态", () => {
  it("从 URL 恢复筛选并清理首尾空白", () => {
    const params = new URLSearchParams(
      "q=%20相机%20&status=ACTIVE&device_type=CAMERA_RIG",
    );

    expect(readDeviceListFilters(params)).toEqual({
      keyword: "相机",
      status: "ACTIVE",
      deviceType: "CAMERA_RIG",
    });
  });

  it("缺失参数回落为空筛选", () => {
    expect(readDeviceListFilters(new URLSearchParams())).toEqual(
      EMPTY_DEVICE_LIST_FILTERS,
    );
  });

  it("筛选变化时正确判断是否存在有效条件", () => {
    expect(hasDeviceListFilters(EMPTY_DEVICE_LIST_FILTERS)).toBe(false);
    expect(
      hasDeviceListFilters({
        keyword: "相机",
        status: "",
        deviceType: "",
      }),
    ).toBe(true);
    expect(
      hasDeviceListFilters({
        keyword: "",
        status: "FAULT",
        deviceType: "",
      }),
    ).toBe(true);
  });

  it("构建链接时携带项目与筛选，并做 URL 编码", () => {
    expect(
      buildDeviceSearch(projectId, {
        keyword: "相机 A",
        status: "ACTIVE",
        deviceType: "CAMERA_RIG",
      }),
    ).toBe(
      `?project_id=${projectId}&q=%E7%9B%B8%E6%9C%BA+A&status=ACTIVE&device_type=CAMERA_RIG`,
    );
  });

  it("无项目与筛选时返回空查询串", () => {
    expect(buildDeviceSearch(null)).toBe("");
  });

  it("详情链接保留筛选并写入非默认 Tab", () => {
    const search = buildDeviceSearch(
      projectId,
      { keyword: "相机", status: "", deviceType: "" },
      "sensors",
    );

    expect(search).toContain(`project_id=${projectId}`);
    expect(search).toContain("q=%E7%9B%B8%E6%9C%BA");
    expect(search).toContain("tab=sensors");
  });

  it("默认 Tab 不写入 URL", () => {
    expect(buildDeviceSearch(projectId, EMPTY_DEVICE_LIST_FILTERS, "overview")).toBe(
      `?project_id=${projectId}`,
    );
  });
});

describe("详情 Tab URL 状态", () => {
  it("白名单内的 Tab 原样恢复", () => {
    expect(resolveDeviceDetailTab("sensors")).toBe("sensors");
    expect(resolveDeviceDetailTab("calibrations")).toBe("calibrations");
    expect(resolveDeviceDetailTab("overview")).toBe("overview");
  });

  it("缺失或非法 Tab 回落概览", () => {
    expect(resolveDeviceDetailTab(null)).toBe(DEFAULT_DEVICE_DETAIL_TAB);
    expect(resolveDeviceDetailTab("unknown")).toBe(DEFAULT_DEVICE_DETAIL_TAB);
    expect(resolveDeviceDetailTab("")).toBe(DEFAULT_DEVICE_DETAIL_TAB);
  });
});
