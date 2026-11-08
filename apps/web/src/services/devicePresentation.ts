export type EquipmentStatusKind = "device" | "sensor" | "calibration";

export interface EnumPresentation {
  label: string;
  color: string;
  /** 服务端返回了未识别值时保留原始码，界面不冒充已知状态。 */
  known: boolean;
}

const UNKNOWN_COLOR = "default";

/**
 * 状态与类型的展示字典。仅用于文案与颜色，不参与权限、动作或状态迁移判断；
 * 动作可用性以服务端返回字段为准。未识别值回退为原始码，兼容契约 minor 升级。
 */
const DEVICE_STATUS_LABELS: Record<string, string> = {
  PROVISIONED: "待启用",
  ACTIVE: "可用",
  IN_MAINTENANCE: "维修中",
  FAULT: "故障",
  SUSPENDED: "已挂起",
  RETIRED: "已退役",
};

const DEVICE_STATUS_COLORS: Record<string, string> = {
  PROVISIONED: "blue",
  ACTIVE: "green",
  IN_MAINTENANCE: "orange",
  FAULT: "red",
  SUSPENDED: "default",
  RETIRED: "default",
};

const CALIBRATION_STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  SUBMITTED: "已提交",
  VALIDATED: "已校验",
  ACTIVE: "生效中",
  SUPERSEDED: "已被替代",
  REJECTED: "已驳回",
  REVOKED: "已作废",
  EXPIRED: "已过期",
};

const CALIBRATION_STATUS_COLORS: Record<string, string> = {
  DRAFT: "default",
  SUBMITTED: "blue",
  VALIDATED: "cyan",
  ACTIVE: "green",
  SUPERSEDED: "default",
  REJECTED: "red",
  REVOKED: "red",
  EXPIRED: "orange",
};

const DEVICE_TYPE_LABELS: Record<string, string> = {
  COMPUTE: "计算单元",
  CONTROLLER: "控制器",
  CAMERA_RIG: "相机组",
  TELEOP: "遥操作设备",
  GRIPPER: "夹爪",
  OTHER: "其他",
};

const SENSOR_TYPE_LABELS: Record<string, string> = {
  RGB: "RGB 相机",
  "RGB-D": "RGB-D 相机",
  DEPTH: "深度相机",
  IMU: "IMU",
  JOINT_STATE: "关节状态",
  END_EFFECTOR: "末端执行器",
  FORCE_TORQUE: "力/力矩",
  MOCAP: "动捕",
  AUDIO: "音频",
};

function normalize(value: string): string {
  return value.trim().toUpperCase();
}

function resolve(
  value: string,
  labels: Record<string, string>,
  colors?: Record<string, string>,
): EnumPresentation {
  const code = normalize(value);
  const label = labels[code];
  if (label === undefined) {
    return {
      label: value.trim() === "" ? "未知" : value,
      color: UNKNOWN_COLOR,
      known: false,
    };
  }
  return {
    label,
    color: colors?.[code] ?? UNKNOWN_COLOR,
    known: true,
  };
}

export function getDeviceStatus(value: string): EnumPresentation {
  return resolve(value, DEVICE_STATUS_LABELS, DEVICE_STATUS_COLORS);
}

export function getSensorStatus(value: string): EnumPresentation {
  // 传感器沿用 DDP-101 §3.1 的设备状态机。
  return getDeviceStatus(value);
}

export function getCalibrationStatus(value: string): EnumPresentation {
  return resolve(value, CALIBRATION_STATUS_LABELS, CALIBRATION_STATUS_COLORS);
}

export function getDeviceType(value: string): EnumPresentation {
  return resolve(value, DEVICE_TYPE_LABELS);
}

export function getSensorType(value: string): EnumPresentation {
  return resolve(value, SENSOR_TYPE_LABELS);
}

export function getStatusPresentation(
  kind: EquipmentStatusKind,
  value: string,
): EnumPresentation {
  if (kind === "calibration") {
    return getCalibrationStatus(value);
  }
  if (kind === "sensor") {
    return getSensorStatus(value);
  }
  return getDeviceStatus(value);
}
