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

function expectContractError(
  action: () => unknown,
  expectedMessage: RegExp,
): void {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(ContractValidationError);
    expect((error as Error).message).toMatch(expectedMessage);
    return;
  }
  throw new Error("预期抛出 ContractValidationError，但未抛出");
}

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
    expect(parsed.sync_mode).toBe("HARDWARE_TRIGGER");
  });

  it("解析标定记录并保留真实证据 UUID", () => {
    const parsed = parseCalibrationRecord(calibration);

    expect(parsed.evidence_asset_ids).toEqual([
      "dddddddd-dddd-7ddd-8ddd-dddddddddddd",
    ]);
    expect(parsed.parameters).toEqual({
      translation_m: [0.1, 0.2, 0.3],
      rotation_quat_wxyz: [1, 0, 0, 0],
      transform_type: "STATIC",
    });
    expect(parsed.uncertainty).toEqual({
      translation_m: 0.001,
      rotation_rad: 0.0001,
    });
  });

  it("允许空证据数组，但拒绝缺失证据字段", () => {
    expect(
      parseCalibrationRecord({ ...calibration, evidence_asset_ids: [] })
        .evidence_asset_ids,
    ).toEqual([]);

    const withoutEvidence: Record<string, unknown> = { ...calibration };
    delete withoutEvidence.evidence_asset_ids;
    expectContractError(
      () => parseCalibrationRecord(withoutEvidence),
      /evidence_asset_ids 必须是数组/,
    );
  });

  it("拒绝非法 UUID，并指出字段路径", () => {
    expectContractError(
      () => parseDevice({ ...device, device_id: "device-1" }),
      /设备\.device_id 必须是 UUID/,
    );
    expectContractError(
      () => parseSensor({ ...sensor, device_id: "asset-1" }),
      /传感器\.device_id 必须是 UUID/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          profile_id: "profile-1",
        }),
      /标定记录\.profile_id 必须是 UUID/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          performed_by: "user-1",
        }),
      /标定记录\.performed_by 必须是 UUID/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          evidence_asset_ids: ["asset-1"],
        }),
      /evidence_asset_ids\[0\] 必须是 UUID/,
    );
  });

  it("拒绝非后端枚举值", () => {
    expectContractError(
      () => parseDevice({ ...device, device_type: "ROBOT" }),
      /设备\.device_type 必须是以下枚举之一/,
    );
    expectContractError(
      () => parseSensor({ ...sensor, sensor_type: "RGB-D" }),
      /传感器\.sensor_type 必须是以下枚举之一/,
    );
    expectContractError(
      () => parseSensor({ ...sensor, sync_mode: "hardware" }),
      /传感器\.sync_mode 必须是以下枚举之一/,
    );
    expectContractError(
      () => parseCalibrationRecord({ ...calibration, scope: "device" }),
      /标定记录\.scope 必须是以下枚举之一/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          calibration_type: "EXTRINSIC",
        }),
      /标定记录\.calibration_type 必须是以下枚举之一/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          target_type: "device",
        }),
      /标定记录\.target_type 必须是以下枚举之一/,
    );
  });

  it("拒绝非法 checksum", () => {
    expectContractError(
      () => parseCalibrationRecord({ ...calibration, checksum: "sha256:abc" }),
      /标定记录\.checksum 必须是 sha256:<64 位小写十六进制>/,
    );
    expectContractError(
      () =>
        parseCalibrationRecord({
          ...calibration,
          checksum: calibration.checksum.toUpperCase(),
        }),
      /标定记录\.checksum 必须是 sha256:<64 位小写十六进制>/,
    );
    expectContractError(
      () => parseCalibrationRecord({ ...calibration, checksum: null }),
      /标定记录\.checksum 必须是字符串/,
    );
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
