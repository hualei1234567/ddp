import { describe, expect, it } from "vitest";

import {
  getCalibrationStatus,
  getDeviceStatus,
  getDeviceType,
  getSensorStatus,
  getSensorType,
  getStatusPresentation,
} from "./devicePresentation";

describe("设备域展示映射", () => {
  it("已知状态映射为中文文案与颜色", () => {
    expect(getDeviceStatus("ACTIVE")).toMatchObject({
      label: "可用",
      color: "green",
      known: true,
    });
    expect(getDeviceStatus("in_maintenance")).toMatchObject({
      label: "维修中",
      known: true,
    });
    expect(getCalibrationStatus("ACTIVE")).toMatchObject({
      label: "生效中",
      color: "green",
    });
  });

  it("未识别状态保留原始码并标记为 unknown", () => {
    expect(getDeviceStatus("FUTURE_STATE")).toMatchObject({
      label: "FUTURE_STATE",
      color: "default",
      known: false,
    });
    expect(getCalibrationStatus("FUTURE_STATE").known).toBe(false);
  });

  it("传感器沿用设备状态机，类型映射独立", () => {
    expect(getSensorStatus("FAULT").label).toBe("故障");
    expect(getDeviceType("CAMERA_RIG").label).toBe("相机组");
    expect(getSensorType("RGB-D").label).toBe("RGB-D 相机");
    expect(getDeviceType("UNKNOWN_TYPE").known).toBe(false);
  });

  it("统一状态入口按 kind 分派", () => {
    expect(getStatusPresentation("device", "RETIRED").label).toBe("已退役");
    expect(getStatusPresentation("calibration", "EXPIRED").label).toBe(
      "已过期",
    );
    expect(getStatusPresentation("sensor", "ACTIVE").label).toBe("可用");
  });
});
