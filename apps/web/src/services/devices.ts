import type {
  CalibrationRecord,
  CursorPage,
  Device,
  Robot,
  Sensor,
} from "../types/device";
import { apiClient, type ApiClient } from "./api";
import {
  parseCalibrationPage,
  parseCalibrationRecord,
  parseDevice,
  parseDevicePage,
  parseRobot,
  parseRobotPage,
  parseSensor,
  parseSensorPage,
} from "./deviceValidation";
import { createIdempotencyKey, formatIfMatch } from "./idempotency";
import {
  asRecord,
  readOptionalStringArray,
} from "./validation";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export type { PageRequestOptions } from "./projectScope";

/**
 * 设备域响应 + 可选的 `allowed_actions`。
 *
 * 设备域契约（`schemas.stage1.yaml`）当前**没有**声明该字段，服务端也不返回；
 * 前端把它作为可选字段读取：一旦服务端补上，动作条自动只渲染服务端给的集合，
 * 不需要再改页面。字段缺失时由动作条显式提示，绝不按 `status` 自行推断。
 */
export type DeviceRecord = Device & { allowed_actions?: string[] };
export type SensorRecord = Sensor & { allowed_actions?: string[] };
export type RobotRecord = Robot & { allowed_actions?: string[] };
export type CalibrationRecordWithActions = CalibrationRecord & {
  allowed_actions?: string[];
};

export interface WriteOptions {
  /** 服务端当前 version；写动作必须带 If-Match。 */
  version: number;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface CreateOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

/** 生命周期动作的请求体；只下发客户端实际填写的字段。 */
export interface DeviceActionPayload {
  reason?: string | null;
  fault_code?: string | null;
  fault_detail?: string | null;
  occurred_at?: string | null;
  maintenance_record_id?: string | null;
}

/** 设备 / 传感器 / 机器人共用的生命周期动作（后端路径段不一致，见映射表）。 */
export const DEVICE_ACTION_VALUES = [
  "activate",
  "enter-maintenance",
  "report-fault",
  "suspend",
  "retire",
] as const;

export type DeviceAction = (typeof DEVICE_ACTION_VALUES)[number];

/** 动作名 → 路由路径段（`enter-maintenance` 走 `/maintenance`，其余同名）。 */
const DEVICE_ACTION_SEGMENTS: Record<DeviceAction, string> = {
  activate: "activate",
  "enter-maintenance": "maintenance",
  "report-fault": "fault",
  suspend: "suspend",
  retire: "retire",
};

/** 标定记录状态动作；请求体为空，只带 If-Match。 */
export const CALIBRATION_ACTION_VALUES = [
  "submit",
  "validate",
  "reject",
  "activate",
  "revoke",
  "expire",
] as const;

export type CalibrationAction = (typeof CALIBRATION_ACTION_VALUES)[number];

export function isDeviceAction(value: string): value is DeviceAction {
  return (DEVICE_ACTION_VALUES as readonly string[]).includes(value);
}

export function isCalibrationAction(
  value: string,
): value is CalibrationAction {
  return (CALIBRATION_ACTION_VALUES as readonly string[]).includes(value);
}

export interface DeviceCreateInput {
  code: string;
  name: string;
  device_type: string;
  robot_id?: string | null;
  vendor?: string | null;
  model?: string | null;
  serial_number?: string | null;
  firmware_version?: string | null;
  hardware_version?: string | null;
  calibration_profile_id?: string | null;
  capability_profile?: Record<string, unknown>;
  location_id?: string | null;
  owner_user_id?: string | null;
}

export type DevicePatchInput = Partial<DeviceCreateInput>;

export interface SensorCreateInput {
  code: string;
  sensor_type: string;
  sample_rate_hz: number;
  /** ADR-0019：frame_id 为权威字段；coordinate_frame 阶段 1 仍必填，由表单同值下发。 */
  coordinate_frame: string;
  frame_id?: string | null;
  frame_convention?: string;
  robot_id?: string | null;
  device_id?: string | null;
  vendor?: string | null;
  model?: string | null;
  serial_number?: string | null;
  resolution_w?: number | null;
  resolution_h?: number | null;
  bit_depth?: number | null;
  channels?: number | null;
  intrinsic_calibration_id?: string | null;
  extrinsic_calibration_id?: string | null;
  sync_mode?: string;
  sync_group?: string | null;
  firmware_version?: string | null;
  calibration_profile_id?: string | null;
  mount_position?: string | null;
}

export type SensorPatchInput = Partial<SensorCreateInput>;

export interface CalibrationCreateInput {
  profile_id: string;
  target_type: string;
  target_id: string;
  calibration_type: string;
  method: string;
  parameters: Record<string, unknown>;
  valid_from: string;
  valid_to?: string | null;
  parent_frame?: string | null;
  child_frame?: string | null;
  evidence_asset_ids?: string[];
  source?: string | null;
  uncertainty?: Record<string, unknown>;
}

export type CalibrationPatchInput = Partial<CalibrationCreateInput>;

export interface RobotCreateInput {
  code: string;
  name: string;
  robot_type: string;
  vendor?: string | null;
  model?: string | null;
  serial_number?: string | null;
  firmware_version?: string | null;
  hardware_version?: string | null;
  calibration_profile_id?: string | null;
  capability_profile?: Record<string, unknown>;
  location_id?: string | null;
  owner_user_id?: string | null;
}

export type RobotPatchInput = Partial<RobotCreateInput>;

export interface RobotListOptions extends PageRequestOptions {
  status?: string;
}

export interface DevicesApi {
  listDevices: (
    projectId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<DeviceRecord>>;
  getDevice: (
    projectId: string,
    deviceId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<DeviceRecord>;
  createDevice: (
    projectId: string,
    input: DeviceCreateInput,
    options?: CreateOptions,
  ) => Promise<DeviceRecord>;
  updateDevice: (
    projectId: string,
    deviceId: string,
    patch: DevicePatchInput,
    options: WriteOptions,
  ) => Promise<DeviceRecord>;
  actOnDevice: (
    projectId: string,
    deviceId: string,
    action: DeviceAction,
    payload: DeviceActionPayload,
    options: WriteOptions,
  ) => Promise<DeviceRecord>;
  listSensors: (
    projectId: string,
    deviceId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<SensorRecord>>;
  createSensor: (
    projectId: string,
    input: SensorCreateInput,
    options?: CreateOptions,
  ) => Promise<SensorRecord>;
  updateSensor: (
    projectId: string,
    sensorId: string,
    patch: SensorPatchInput,
    options: WriteOptions,
  ) => Promise<SensorRecord>;
  actOnSensor: (
    projectId: string,
    sensorId: string,
    action: DeviceAction,
    payload: DeviceActionPayload,
    options: WriteOptions,
  ) => Promise<SensorRecord>;
  listCalibrations: (
    projectId: string,
    deviceId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<CalibrationRecordWithActions>>;
  createCalibration: (
    projectId: string,
    input: CalibrationCreateInput,
    options?: CreateOptions,
  ) => Promise<CalibrationRecordWithActions>;
  updateCalibration: (
    projectId: string,
    calibrationId: string,
    patch: CalibrationPatchInput,
    options: WriteOptions,
  ) => Promise<CalibrationRecordWithActions>;
  actOnCalibration: (
    projectId: string,
    calibrationId: string,
    action: CalibrationAction,
    options: WriteOptions,
  ) => Promise<CalibrationRecordWithActions>;
  listRobots: (
    projectId: string,
    options?: RobotListOptions,
  ) => Promise<CursorPage<RobotRecord>>;
  getRobot: (
    projectId: string,
    robotId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<RobotRecord>;
  createRobot: (
    projectId: string,
    input: RobotCreateInput,
    options?: CreateOptions,
  ) => Promise<RobotRecord>;
  updateRobot: (
    projectId: string,
    robotId: string,
    patch: RobotPatchInput,
    options: WriteOptions,
  ) => Promise<RobotRecord>;
  actOnRobot: (
    projectId: string,
    robotId: string,
    action: DeviceAction,
    payload: DeviceActionPayload,
    options: WriteOptions,
  ) => Promise<RobotRecord>;
}

function readAllowedActions(value: unknown): string[] | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return undefined;
  }
  return (
    readOptionalStringArray(
      value as Record<string, unknown>,
      "allowed_actions",
      "allowed_actions",
    ) ?? undefined
  );
}

function withActions<T>(item: T, raw: unknown): T & { allowed_actions?: string[] } {
  const actions = readAllowedActions(raw);
  if (actions === undefined) {
    return item as T & { allowed_actions?: string[] };
  }
  return { ...item, allowed_actions: actions } as T & {
    allowed_actions?: string[];
  };
}

function withPageActions<T>(
  page: CursorPage<T>,
  rawValue: unknown,
  path: string,
): CursorPage<T & { allowed_actions?: string[] }> {
  const rawItems = asRecord(rawValue, path).items;
  const items = Array.isArray(rawItems) ? rawItems : [];
  return {
    ...page,
    items: page.items.map((item, index) => withActions(item, items[index])),
  };
}

function actionBody(payload: DeviceActionPayload): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (payload.reason !== undefined) {
    body.reason = payload.reason;
  }
  if (payload.fault_code !== undefined) {
    body.fault_code = payload.fault_code;
  }
  if (payload.fault_detail !== undefined) {
    body.fault_detail = payload.fault_detail;
  }
  if (payload.occurred_at !== undefined) {
    body.occurred_at = payload.occurred_at;
  }
  if (payload.maintenance_record_id !== undefined) {
    body.maintenance_record_id = payload.maintenance_record_id;
  }
  return body;
}

function createHeaders(options: CreateOptions | undefined): Record<string, string> {
  return {
    "Idempotency-Key": options?.idempotencyKey ?? createIdempotencyKey(),
  };
}

/** PATCH 只声明 If-Match；动作额外要求幂等键。 */
function writeHeaders(
  options: WriteOptions,
  behavior: { idempotent: boolean },
): Record<string, string> {
  const headers: Record<string, string> = {
    "If-Match": formatIfMatch(options.version),
  };
  if (behavior.idempotent) {
    headers["Idempotency-Key"] = options.idempotencyKey ?? createIdempotencyKey();
  }
  return headers;
}

/**
 * 设备域 API。路径与错误码遵循 DDP-101 §4.3；
 * 前端只做响应结构校验与项目归属一致性检查，权限以服务端为准。
 */
export function createDevicesApi(client: ApiClient): DevicesApi {
  return {
    async listDevices(projectId, options) {
      return client.get(projectPath(projectId, "/devices"), {
        signal: options?.signal,
        query: pageQuery(options),
        validate: (value) =>
          assertPageScope(
            withPageActions(parseDevicePage(value), value, "设备列表"),
            projectId,
            "设备列表",
          ),
      });
    },

    async getDevice(projectId, deviceId, options) {
      return client.get(
        projectPath(
          projectId,
          `/devices/${requireIdentifier(deviceId, "device_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              withActions(parseDevice(value), value),
              projectId,
              "设备",
            ),
        },
      );
    },

    async createDevice(projectId, input, options) {
      return client.request(projectPath(projectId, "/devices"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            withActions(parseDevice(value), value),
            projectId,
            "设备",
          ),
      });
    },

    async updateDevice(projectId, deviceId, patch, options) {
      return client.request(
        projectPath(
          projectId,
          `/devices/${requireIdentifier(deviceId, "device_id")}`,
        ),
        {
          method: "PATCH",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: false }),
          body: patch,
          validate: (value) =>
            assertProjectScope(
              withActions(parseDevice(value), value),
              projectId,
              "设备",
            ),
        },
      );
    },

    async actOnDevice(projectId, deviceId, action, payload, options) {
      return client.request(
        projectPath(
          projectId,
          `/devices/${requireIdentifier(deviceId, "device_id")}/${requireIdentifier(
            DEVICE_ACTION_SEGMENTS[action],
            "action",
          )}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: true }),
          body: actionBody(payload),
          validate: (value) =>
            assertProjectScope(
              withActions(parseDevice(value), value),
              projectId,
              "设备",
            ),
        },
      );
    },

    async listSensors(projectId, deviceId, options) {
      return client.get(projectPath(projectId, "/sensors"), {
        signal: options?.signal,
        query: {
          device_id: deviceId,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            withPageActions(parseSensorPage(value), value, "传感器列表"),
            projectId,
            "传感器列表",
          ),
      });
    },

    async createSensor(projectId, input, options) {
      return client.request(projectPath(projectId, "/sensors"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            withActions(parseSensor(value), value),
            projectId,
            "传感器",
          ),
      });
    },

    async updateSensor(projectId, sensorId, patch, options) {
      return client.request(
        projectPath(
          projectId,
          `/sensors/${requireIdentifier(sensorId, "sensor_id")}`,
        ),
        {
          method: "PATCH",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: false }),
          body: patch,
          validate: (value) =>
            assertProjectScope(
              withActions(parseSensor(value), value),
              projectId,
              "传感器",
            ),
        },
      );
    },

    async actOnSensor(projectId, sensorId, action, payload, options) {
      return client.request(
        projectPath(
          projectId,
          `/sensors/${requireIdentifier(sensorId, "sensor_id")}/${requireIdentifier(
            DEVICE_ACTION_SEGMENTS[action],
            "action",
          )}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: true }),
          body: actionBody(payload),
          validate: (value) =>
            assertProjectScope(
              withActions(parseSensor(value), value),
              projectId,
              "传感器",
            ),
        },
      );
    },

    async listCalibrations(projectId, deviceId, options) {
      return client.get(projectPath(projectId, "/calibrations"), {
        signal: options?.signal,
        query: {
          target_type: "DEVICE",
          target_id: deviceId,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            withPageActions(
              parseCalibrationPage(value),
              value,
              "标定记录列表",
            ),
            projectId,
            "标定记录列表",
          ),
      });
    },

    async createCalibration(projectId, input, options) {
      return client.request(projectPath(projectId, "/calibrations"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            parseCalibrationRecord(value),
            projectId,
            "标定记录",
          ),
      });
    },

    async updateCalibration(projectId, calibrationId, patch, options) {
      return client.request(
        projectPath(
          projectId,
          `/calibrations/${requireIdentifier(calibrationId, "calibration_id")}`,
        ),
        {
          method: "PATCH",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: false }),
          body: patch,
          validate: (value) =>
            assertProjectScope(
              withActions(parseCalibrationRecord(value), value),
              projectId,
              "标定记录",
            ),
        },
      );
    },

    async actOnCalibration(projectId, calibrationId, action, options) {
      return client.request(
        projectPath(
          projectId,
          `/calibrations/${requireIdentifier(calibrationId, "calibration_id")}/${requireIdentifier(
            action,
            "action",
          )}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          // 标定动作路由没有 Idempotency-Key 依赖，只要求 If-Match。
          headers: writeHeaders(options, { idempotent: false }),
          validate: (value) =>
            assertProjectScope(
              withActions(parseCalibrationRecord(value), value),
              projectId,
              "标定记录",
            ),
        },
      );
    },

    async listRobots(projectId, options) {
      return client.get(projectPath(projectId, "/robots"), {
        signal: options?.signal,
        query: {
          status: options?.status,
          ...pageQuery(options),
        },
        validate: (value) =>
          assertPageScope(
            withPageActions(parseRobotPage(value), value, "机器人列表"),
            projectId,
            "机器人列表",
          ),
      });
    },

    async getRobot(projectId, robotId, options) {
      return client.get(
        projectPath(
          projectId,
          `/robots/${requireIdentifier(robotId, "robot_id")}`,
        ),
        {
          signal: options?.signal,
          validate: (value) =>
            assertProjectScope(
              withActions(parseRobot(value), value),
              projectId,
              "机器人",
            ),
        },
      );
    },

    async createRobot(projectId, input, options) {
      return client.request(projectPath(projectId, "/robots"), {
        method: "POST",
        signal: options?.signal,
        headers: createHeaders(options),
        body: input,
        validate: (value) =>
          assertProjectScope(
            withActions(parseRobot(value), value),
            projectId,
            "机器人",
          ),
      });
    },

    async updateRobot(projectId, robotId, patch, options) {
      return client.request(
        projectPath(
          projectId,
          `/robots/${requireIdentifier(robotId, "robot_id")}`,
        ),
        {
          method: "PATCH",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: false }),
          body: patch,
          validate: (value) =>
            assertProjectScope(
              withActions(parseRobot(value), value),
              projectId,
              "机器人",
            ),
        },
      );
    },

    async actOnRobot(projectId, robotId, action, payload, options) {
      return client.request(
        projectPath(
          projectId,
          `/robots/${requireIdentifier(robotId, "robot_id")}/${requireIdentifier(
            DEVICE_ACTION_SEGMENTS[action],
            "action",
          )}`,
        ),
        {
          method: "POST",
          signal: options.signal,
          headers: writeHeaders(options, { idempotent: true }),
          body: actionBody(payload),
          validate: (value) =>
            assertProjectScope(
              withActions(parseRobot(value), value),
              projectId,
              "机器人",
            ),
        },
      );
    },
  };
}

export const devicesApi = createDevicesApi(apiClient);

/**
 * 表单里的 JSON 对象文本（`capability_profile` / 标定 `parameters`）。
 *
 * 契约只声明这些字段是「对象」，不约束内部键；因此这里只校验"是合法 JSON 对象"，
 * 不解释业务语义。空文本返回 `null`，由调用方决定字段是否下发。
 */
export function parseJsonObjectText(
  text: string,
  label: string,
): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label}不是合法的 JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象`);
  }
  return parsed as Record<string, unknown>;
}

