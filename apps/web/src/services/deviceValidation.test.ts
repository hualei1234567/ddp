import { describe, expect, it } from "vitest";

import {
  parseCalibrationRecord,
  parseDevice,
  parseDevicePage,
  parseSensor,
} from "./deviceValidation";
import { ContractValidationError } from "./validation";

const device = {
  device_id: "dev-1",
  project_id: "project-1",
  robot_id: null,
  code: "DEV-001",
  name: "主控计算单元",
  device_type: "COMPUTE",
  vendor: "Acme",
  model: "X1",
  serial_number: "SN-001",
  firmware_version: "1.0.0",
  hardware_version: "rev-a",
  calibration_profile_id: null,
  capability_profile: { dof: 7, interfaces: ["can"] },
  status: "ACTIVE",
  location_id: null,
  owner_user_id: null,
  version: 3,
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T01:00:00Z",
  created_by: null,
  updated_by: null,
};

const sensor = {
  sensor_id: "sensor-1",
  project_id: "project-1",
  robot_id: null,
  device_id: "dev-1",
  code: "CAM-001",
  sensor_type: "RGB",
  vendor: "Acme",
  model: "Cam",
  serial_number: "SN-CAM-1",
  sample_rate_hz: 30,
  resolution_w: 1920,
  resolution_h: 1080,
  bit_depth: 8,
  channels: ["rgb"],
  intrinsic_calibration_id: null,
  extrinsic_calibration_id: null,
  coordinate_frame: "camera_link",
  sync_mode: "hardware",
  sync_group: "rig-a",
  firmware_version: null,
  calibration_profile_id: null,
  status: "ACTIVE",
  mount_position: "front",
  version: 1,
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T00:00:00Z",
};

const calibration = {
  calibration_id: "cal-1",
  project_id: "project-1",
  profile_id: "profile-1",
  scope: "device",
  target_type: "DEVICE",
  target_id: "dev-1",
  calibration_type: "EXTRINSIC",
  method: "charuco",
  parameters: { reprojection_error: 0.2 },
  parent_frame: "base_link",
  child_frame: "camera_link",
  valid_from: "2026-11-08T00:00:00Z",
  valid_to: null,
  status: "ACTIVE",
  evidence_asset_ids: ["asset-1"],
  source: "manual",
  checksum: "sha256:abc",
  uncertainty: 0.2,
  performed_by: "user-1",
  validated_by: "user-2",
  validated_at: "2026-11-08T02:00:00Z",
  supersedes_id: null,
  version: 2,
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T02:00:00Z",
};

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

  it("解析传感器可选数值与通道数组", () => {
    const parsed = parseSensor(sensor);

    expect(parsed.sample_rate_hz).toBe(30);
    expect(parsed.resolution_w).toBe(1920);
    expect(parsed.channels).toEqual(["rgb"]);
  });

  it("解析标定记录，并为缺失证据数组提供空数组", () => {
    const withoutEvidence: Record<string, unknown> = { ...calibration };
    delete withoutEvidence.evidence_asset_ids;
    const parsed = parseCalibrationRecord(withoutEvidence);

    expect(parsed.evidence_asset_ids).toEqual([]);
    expect(parsed.parameters).toEqual({ reprojection_error: 0.2 });
  });
});
