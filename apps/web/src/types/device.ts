/**
 * 设备域契约类型，对应 DDP-101《阶段 1 领域模型与 API/数据契约草案》§2.2。
 *
 * 字段名保持与契约一致；状态类字段使用 string，而不是封闭枚举，
 * 这样服务端新增状态时前端仍能展示原始值，不会因契约 minor 升级直接报错。
 */
export interface Device {
  device_id: string;
  project_id: string;
  robot_id: string | null;
  code: string;
  name: string;
  device_type: string;
  vendor: string | null;
  model: string | null;
  serial_number: string | null;
  firmware_version: string | null;
  hardware_version: string | null;
  calibration_profile_id: string | null;
  /** 能力描述结构由后续契约冻结，前端只展示存在性与顶层键，不解释业务语义。 */
  capability_profile: Record<string, unknown> | null;
  status: string;
  location_id: string | null;
  owner_user_id: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
  updated_by: string | null;
}

export interface Sensor {
  sensor_id: string;
  project_id: string;
  robot_id: string | null;
  device_id: string | null;
  code: string;
  sensor_type: string;
  vendor: string | null;
  model: string | null;
  serial_number: string | null;
  sample_rate_hz: number | null;
  resolution_w: number | null;
  resolution_h: number | null;
  bit_depth: number | null;
  /** 图像通道数，后端为整数（如 RGB=3、RGB_D=4），不是通道名数组。 */
  channels: number | null;
  intrinsic_calibration_id: string | null;
  extrinsic_calibration_id: string | null;
  coordinate_frame: string | null;
  sync_mode: string | null;
  sync_group: string | null;
  firmware_version: string | null;
  calibration_profile_id: string | null;
  status: string;
  mount_position: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CalibrationRecord {
  calibration_id: string;
  project_id: string;
  profile_id: string | null;
  scope: string | null;
  target_type: string;
  target_id: string;
  calibration_type: string;
  method: string | null;
  /** 标定参数由 Canonical Schema 约束，前端不解释具体字段含义。 */
  parameters: Record<string, unknown> | null;
  parent_frame: string | null;
  child_frame: string | null;
  valid_from: string | null;
  valid_to: string | null;
  status: string;
  evidence_asset_ids: string[];
  source: string | null;
  checksum: string | null;
  /**
   * 不确定度，后端序列化为 JSON 对象（如 `{ translation_m, rotation_rad }`）。
   * 具体键由 Canonical 标定 Schema 约束，前端不解释业务语义。
   */
  uncertainty: Record<string, unknown> | null;
  performed_by: string | null;
  validated_by: string | null;
  validated_at: string | null;
  supersedes_id: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

/** DDP-101 §4.1 约定：列表统一返回 `{ items, next_cursor }`。 */
export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}
