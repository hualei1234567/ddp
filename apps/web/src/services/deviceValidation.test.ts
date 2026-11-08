import { describe, expect, it } from "vitest";

import {
  backendCalibrationFixture,
  backendDeviceFixture,
  backendSensorFixture,
} from "./__fixtures__/deviceContract.fixtures";
import {
  parseCalibrationRecord,
  parseDevice,
  parseDevicePage,
  parseSensor,
} from "./deviceValidation";
import { ContractValidationError } from "./validation";

const device = backendDeviceFixture;
const sensor = backendSensorFixture;
const calibration = backendCalibrationFixture;

describe("设备域契约校验", () => {
  it("解析设备并保留未知状态码，避免契约 minor 升级直接报错", () => {
    const parsed = parseDevice({ ...device, status: "FUTURE_STATE" });

    expect(parsed.status).toBe("FUTURE_STATE");
    expect(parsed.capability_profile).toEqual({
      dof: 7,
      interfaces: ["can"],
    });
  });

  it("拒绝缺少必需字段的设备", () => {
    const withoutId: Record<string, unknown> = { ...device };
    delete withoutId.device_id;

    expect(() => parseDevice(withoutId)).toThrow(ContractValidationError);
  });

  it("解析游标分页并区分 null 与非法 next_cursor", () => {
    expect(
      parseDevicePage({ items: [device], next_cursor: null }),
    ).toMatchObject({ next_cursor: null });

    expect(() =>
      parseDevicePage({ items: [device], next_cursor: 1 }),
    ).toThrow(ContractValidationError);
  });

  it("按后端序列化形状解析传感器数值通道", () => {
    const parsed = parseSensor(sensor);

    expect(parsed.sample_rate_hz).toBe(30);
    expect(parsed.resolution_w).toBe(1920);
    expect(parsed.channels).toBe(4);
    expect(parsed.sensor_type).toBe("RGB_D");
  });

  it("解析标定记录，并为缺失证据数组提供空数组", () => {
    const withoutEvidence: Record<string, unknown> = { ...calibration };
    delete withoutEvidence.evidence_asset_ids;
    const parsed = parseCalibrationRecord(withoutEvidence);

    expect(parsed.evidence_asset_ids).toEqual([]);
    expect(parsed.parameters).toEqual({ reprojection_error: 0.2 });
    expect(parsed.uncertainty).toEqual({
      translation_m: 0.001,
      rotation_rad: 0.002,
    });
  });

  it("拒绝历史错误形状：channels 不能是字符串数组", () => {
    expect(() => parseSensor({ ...sensor, channels: ["rgb"] })).toThrow(
      ContractValidationError,
    );
  });

  it("拒绝历史错误形状：uncertainty 不能是数字", () => {
    expect(() =>
      parseCalibrationRecord({ ...calibration, uncertainty: 0.2 }),
    ).toThrow(ContractValidationError);
  });

  it("channels 与 uncertainty 允许为 null", () => {
    expect(parseSensor({ ...sensor, channels: null }).channels).toBeNull();
    expect(
      parseCalibrationRecord({ ...calibration, uncertainty: null }).uncertainty,
    ).toBeNull();
  });
});
