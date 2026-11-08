import { describe, expect, it } from "vitest";

import {
  backendCalibrationPageFixture,
  backendSensorPageFixture,
} from "./__fixtures__/deviceContract.fixtures";
import { getSensorType } from "./devicePresentation";
import { parseCalibrationPage, parseSensorPage } from "./deviceValidation";

/**
 * DDP-206 契约测试：直接使用后端真实序列化形状的 fixture。
 * 目的不是重复解析器单测，而是锁定前后端字段类型与枚举的一致性。
 */
describe("设备域后端契约对齐", () => {
  it("传感器分页使用真实 fixture：channels 为整数、sensor_type 为 RGB_D", () => {
    const page = parseSensorPage(backendSensorPageFixture);
    const [sensor] = page.items;

    expect(sensor).toBeDefined();
    expect(typeof sensor?.channels).toBe("number");
    expect(sensor?.channels).toBe(4);
    expect(sensor?.sensor_type).toBe("RGB_D");
  });

  it("RGB_D 枚举正确显示为 RGB-D 相机", () => {
    expect(getSensorType("RGB_D")).toMatchObject({
      label: "RGB-D 相机",
      known: true,
    });
  });

  it("兼容历史连字符值 RGB-D，不改变后端枚举", () => {
    expect(getSensorType("RGB-D")).toMatchObject({
      label: "RGB-D 相机",
      known: true,
    });
    expect(getSensorType("rgb-d").known).toBe(true);
  });

  it("标定分页使用真实 fixture：uncertainty 为 JSON 对象", () => {
    const page = parseCalibrationPage(backendCalibrationPageFixture);
    const [calibration] = page.items;

    expect(calibration).toBeDefined();
    expect(typeof calibration?.uncertainty).toBe("object");
    expect(Array.isArray(calibration?.uncertainty)).toBe(false);
    expect(calibration?.uncertainty).toEqual({
      translation_m: 0.001,
      rotation_rad: 0.002,
    });
  });
});
