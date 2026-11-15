/**
 * 设备域契约类型，对应 DDP-101《阶段 1 领域模型与 API/数据契约草案》§2.2。
 *
 * 字段名保持与契约一致；状态类字段使用 string，而不是封闭枚举，
 * 这样服务端新增状态时前端仍能展示原始值，不会因契约 minor 升级直接报错。
 * 结构型枚举在 `deviceValidation` 入参处校验，避免非法值进入 UI。
 */

/** 后端领域枚举权威值；用于响应校验，不用于推断状态流转。 */
export const DEVICE_TYPE_VALUES = [
  "COMPUTE",
  "CONTROLLER",
  "CAMERA_RIG",
  "TELEOP",
  "GRIPPER",
  "OTHER",
] as const;

export const SENSOR_TYPE_VALUES = [
  "RGB",
  "RGB_D",
  "DEPTH",
  "IMU",
  "JOINT_STATE",
  "END_EFFECTOR",
  "FORCE_TORQUE",
  "MOCAP",
  "AUDIO",
] as const;

/**
 * 坐标系约定（ADR-0019）。`UNSPECIFIED` 是显式的"来源未声明"，
 * 不是前端猜出的默认值——展示时应如实呈现为"未声明"。
 */
export const FRAME_CONVENTION_VALUES = [
  "ROS_REP_103",
  "OPENCV",
  "UNITY_LHS",
  "UNSPECIFIED",
] as const;

export const SENSOR_SYNC_MODE_VALUES = [
  "PTP",
  "NTP",
  "HARDWARE_TRIGGER",
  "SOFTWARE_ESTIMATE",
  "MANUAL",
  "NONE",
] as const;

export const CALIBRATION_SCOPE_VALUES = [
  "ROBOT",
  "DEVICE",
  "SENSOR",
  "SYSTEM",
] as const;

export const CALIBRATION_TARGET_TYPE_VALUES = [
  "ROBOT",
  "DEVICE",
  "SENSOR",
  "SYSTEM",
] as const;

export const CALIBRATION_TYPE_VALUES = [
  "INTRINSICS",
  "EXTRINSICS",
  "HAND_EYE",
  "IMU",
  "JOINT_OFFSET",
  "TIME_OFFSET",
  "FORCE_TORQUE",
  "DEPTH_ALIGNMENT",
] as const;

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
  /**
   * 以下为服务端新鲜度元数据（契约冻结前按可选字段读取）。
   * 阈值一律由服务端下发；前端只做存在性判断，不提供默认超时时间，
   * 避免把旧数据误判为实时数据或把正常数据误判为过期。
   */
  freshness?: string | null;
  last_synced_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_timeout_seconds?: number | null;
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
  /** 坐标系标识（ADR-0019）。阶段 1 与 coordinate_frame 等价。 */
  frame_id: string | null;
  /** 坐标约定；UNSPECIFIED 表示来源未声明，不是猜出的默认值。 */
  frame_convention: string;
  /** @deprecated ADR-0019 阶段 2 移除；请读 frame_id。 */
  coordinate_frame: string | null;
  /** 同步方式，后端枚举见 `SENSOR_SYNC_MODE_VALUES`。 */
  sync_mode: string;
  sync_group: string | null;
  firmware_version: string | null;
  calibration_profile_id: string | null;
  status: string;
  mount_position: string | null;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface Robot {
  robot_id: string;
  project_id: string;
  code: string;
  name: string;
  robot_type: string;
  status: string;
  vendor: string | null;
  model: string | null;
  serial_number: string | null;
  firmware_version: string | null;
  hardware_version: string | null;
  calibration_profile_id: string | null;
  capability_profile: Record<string, unknown>;
  location_id: string | null;
  owner_user_id: string | null;
  status_reason: string | null;
  fault_code: string | null;
  fault_detail: string | null;
  fault_occurred_at: string | null;
  status_changed_at: string | null;
  status_changed_by: string | null;
  version: number;
}

/**
 * 标定配置的**一个不可变版本**（ADR-0020 / G-07）。
 * 改配置 = 新建版本；前端没有"编辑 Profile"的概念。
 */
export interface CalibrationProfile {
  profile_id: string;
  project_id: string;
  name: string;
  scope: string;
  target_type: string;
  /** 业务版本号；服务端表列仍叫 version，对外统一为 profile_version。 */
  profile_version: number;
  schema_version: string;
  required_types: string[];
  selection_rule: Record<string, unknown>;
  target_id: string | null;
}

export interface CalibrationRecord {
  calibration_id: string;
  project_id: string;
  profile_id: string;
  scope: string;
  target_type: string;
  target_id: string;
  calibration_type: string;
  method: string;
  /** 标定参数由 Canonical Schema 约束，前端不解释具体字段含义。 */
  parameters: Record<string, unknown> | null;
  parent_frame: string | null;
  child_frame: string | null;
  valid_from: string | null;
  valid_to: string | null;
  status: string;
  evidence_asset_ids: string[];
  source: string | null;
  checksum: string;
  /**
   * 不确定度，后端序列化为 JSON 对象（如 `{ translation_m, rotation_rad }`）。
   * 具体键由 Canonical 标定 Schema 约束，前端不解释业务语义。
   */
  uncertainty: Record<string, unknown> | null;
  performed_by: string;
  validated_by: string | null;
  validated_at: string | null;
  supersedes_id: string | null;
  /**
   * 业务修订号（ADR-0020）：只有内容变更才递增，状态迁移不动它。
   * 与 `version`（乐观锁）语义不同，不能互相替代。
   */
  calibration_version: number;
  /** 创建时 Profile 版本快照（ADR-0020 / G-05）。 */
  profile_version: number | null;
  profile_schema_version: string | null;
  /**
   * 创建时 Profile 内容副本，保证 Profile 事后被改也能复现本次标定。
   * 具体键由服务端写入，前端只展示不解释。
   */
  profile_snapshot: Record<string, unknown>;
  /** DEPRECATED（ADR-0020）：乐观锁版本，用于 If-Match；阶段 2 改名 lock_version。 */
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export type { CursorPage } from "./api";
