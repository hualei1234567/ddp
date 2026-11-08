import { describe, expect, it } from "vitest";

import {
  backendCalibrationPageFixture,
  backendCalibrationFixture,
  backendDeviceFixture,
  backendSensorPageFixture,
  backendSensorFixture,
} from "./__fixtures__/deviceContract.fixtures";
import snapshot from "./__fixtures__/deviceContract.snapshot.json";
import { getSensorType } from "./devicePresentation";
import {
  parseCalibrationPage,
  parseDevice,
  parseSensorPage,
} from "./deviceValidation";

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^sha256:[0-9a-f]{64}$/;

/**
 * DDP-213 契约测试：fixture 必须是真实 FastAPI 响应快照。
 * 除本文件外，`npm run contract:check` 会重新调用后端并逐值比较快照，
 * 后端字段或序列化漂移时会失败。
 */
describe("设备域后端契约对齐", () => {
  it("fixture 直接来自真实响应快照", () => {
    expect(backendDeviceFixture).toEqual(snapshot.device);
    expect(backendSensorFixture).toEqual(snapshot.sensor);
    expect(backendCalibrationFixture).toEqual(snapshot.calibration);
    expect(backendSensorPageFixture).toEqual(snapshot.sensor_page);
    expect(backendCalibrationPageFixture).toEqual(snapshot.calibration_page);
  });

  it("设备字段集合与后端序列化一致", () => {
    expect(Object.keys(backendDeviceFixture).sort()).toEqual(
      [
        "calibration_profile_id",
        "capability_profile",
        "code",
        "created_at",
        "created_by",
        "device_id",
        "device_type",
        "firmware_version",
        "hardware_version",
        "location_id",
        "model",
        "name",
        "owner_user_id",
        "project_id",
        "robot_id",
        "serial_number",
        "status",
        "updated_at",
        "updated_by",
        "vendor",
        "version",
      ].sort(),
    );
  });

  it("传感器字段集合与后端序列化一致", () => {
    expect(Object.keys(backendSensorFixture).sort()).toEqual(
      [
        "bit_depth",
        "calibration_profile_id",
        "channels",
        "code",
        "coordinate_frame",
        "created_at",
        "device_id",
        "extrinsic_calibration_id",
        "firmware_version",
        "intrinsic_calibration_id",
        "model",
        "mount_position",
        "project_id",
        "resolution_h",
        "resolution_w",
        "robot_id",
        "sample_rate_hz",
        "sensor_id",
        "sensor_type",
        "serial_number",
        "status",
        "sync_group",
        "sync_mode",
        "updated_at",
        "vendor",
        "version",
      ].sort(),
    );
  });

  it("标定字段集合与后端序列化一致", () => {
    expect(Object.keys(backendCalibrationFixture).sort()).toEqual(
      [
        "calibration_id",
        "calibration_type",
        "checksum",
        "child_frame",
        "created_at",
        "evidence_asset_ids",
        "method",
        "parent_frame",
        "parameters",
        "performed_by",
        "profile_id",
        "project_id",
        "scope",
        "source",
        "status",
        "supersedes_id",
        "target_id",
        "target_type",
        "uncertainty",
        "updated_at",
        "valid_from",
        "valid_to",
        "validated_at",
        "validated_by",
        "version",
      ].sort(),
    );
  });

  it("传感器分页使用真实值：channels 为整数、sensor_type 为 RGB_D、sync_mode 为大写枚举", () => {
    const page = parseSensorPage(backendSensorPageFixture);
    const [sensor] = page.items;

    expect(sensor).toBeDefined();
    expect(sensor?.channels).toBe(4);
    expect(sensor?.sensor_type).toBe("RGB_D");
    expect(sensor?.sync_mode).toBe("HARDWARE_TRIGGER");
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

  it("标定分页使用真实值：scope/calibration_type 为大写枚举，uncertainty 为 JSON 对象", () => {
    const page = parseCalibrationPage(backendCalibrationPageFixture);
    const [calibration] = page.items;

    expect(calibration).toBeDefined();
    expect(calibration?.scope).toBe("DEVICE");
    expect(calibration?.target_type).toBe("DEVICE");
    expect(calibration?.calibration_type).toBe("EXTRINSICS");
    expect(calibration?.uncertainty).toEqual({
      translation_m: 0.001,
      rotation_rad: 0.0001,
    });
  });

  it("UUID、枚举与 sha256 字段均满足后端真实格式", () => {
    expect(backendCalibrationFixture.profile_id).toMatch(UUID_PATTERN);
    expect(backendCalibrationFixture.performed_by).toMatch(UUID_PATTERN);
    expect(backendCalibrationFixture.target_id).toMatch(UUID_PATTERN);
    expect(
      backendCalibrationFixture.evidence_asset_ids.every((id) =>
        UUID_PATTERN.test(id),
      ),
    ).toBe(true);
    expect(backendCalibrationFixture.checksum).toMatch(SHA256_PATTERN);
    expect(backendSensorFixture.sync_mode).toBe("HARDWARE_TRIGGER");
    expect(backendDeviceFixture.device_type).toBe("COMPUTE");
  });

  it("真实设备响应可以进入解析器", () => {
    expect(parseDevice(backendDeviceFixture)).toMatchObject(
      backendDeviceFixture,
    );
  });
});
