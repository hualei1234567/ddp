/**
 * 设备域契约 fixture（DDP-206）。
 *
 * 来源：DDP-101《阶段 1 领域模型与 API/数据契约草案》§2.2、
 * 《Canonical 草案》§4.3，以及后端设备域真实序列化形状：
 * - `Sensor.channels` 为整数（图像通道数），不是通道名数组；
 * - `CalibrationRecord.uncertainty` 为 JSON 对象；
 * - `Sensor.sensor_type` 使用后端枚举 `RGB_D`。
 *
 * 用途：仅作为前端契约解析测试输入，不是生产数据。
 * 后端设备域实现合并后，应以真实响应更新本文件。
 */
const PROJECT_ID = "11111111-1111-7111-8111-111111111111";

export const backendDeviceFixture = {
  device_id: "22222222-2222-7222-8222-222222222222",
  project_id: PROJECT_ID,
  robot_id: "33333333-3333-7333-8333-333333333333",
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

export const backendSensorFixture = {
  sensor_id: "44444444-4444-7444-8444-444444444444",
  project_id: PROJECT_ID,
  robot_id: "33333333-3333-7333-8333-333333333333",
  device_id: backendDeviceFixture.device_id,
  code: "CAM-001",
  sensor_type: "RGB_D",
  vendor: "Acme",
  model: "DepthCam",
  serial_number: "SN-CAM-1",
  sample_rate_hz: 30,
  resolution_w: 1920,
  resolution_h: 1080,
  bit_depth: 8,
  channels: 4,
  intrinsic_calibration_id: null,
  extrinsic_calibration_id: null,
  coordinate_frame: "44444444-4444-7444-8444-444444444444/optical_frame",
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

export const backendCalibrationFixture = {
  calibration_id: "55555555-5555-7555-8555-555555555555",
  project_id: PROJECT_ID,
  profile_id: "profile-1",
  scope: "device",
  target_type: "DEVICE",
  target_id: backendDeviceFixture.device_id,
  calibration_type: "EXTRINSIC",
  method: "charuco",
  parameters: { reprojection_error: 0.2 },
  parent_frame: "base_link",
  child_frame: "44444444-4444-7444-8444-444444444444/optical_frame",
  valid_from: "2026-11-08T00:00:00Z",
  valid_to: null,
  status: "ACTIVE",
  evidence_asset_ids: ["asset-1"],
  source: "manual",
  checksum: "sha256:abc",
  uncertainty: { translation_m: 0.001, rotation_rad: 0.002 },
  performed_by: "user-1",
  validated_by: "user-2",
  validated_at: "2026-11-08T02:00:00Z",
  supersedes_id: null,
  version: 2,
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T02:00:00Z",
};

export const backendSensorPageFixture = {
  items: [backendSensorFixture],
  next_cursor: null,
};

export const backendCalibrationPageFixture = {
  items: [backendCalibrationFixture],
  next_cursor: null,
};
