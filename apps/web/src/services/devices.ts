import type {
  CalibrationRecord,
  CursorPage,
  Device,
  Sensor,
} from "../types/device";
import { apiClient, type ApiClient } from "./api";
import {
  parseCalibrationPage,
  parseDevice,
  parseDevicePage,
  parseSensorPage,
} from "./deviceValidation";
import {
  assertPageScope,
  assertProjectScope,
  pageQuery,
  projectPath,
  requireIdentifier,
  type PageRequestOptions,
} from "./projectScope";

export type { PageRequestOptions } from "./projectScope";

export interface DevicesApi {
  listDevices: (
    projectId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<Device>>;
  getDevice: (
    projectId: string,
    deviceId: string,
    options?: Pick<PageRequestOptions, "signal">,
  ) => Promise<Device>;
  listSensors: (
    projectId: string,
    deviceId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<Sensor>>;
  listCalibrations: (
    projectId: string,
    deviceId: string,
    options?: PageRequestOptions,
  ) => Promise<CursorPage<CalibrationRecord>>;
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
          assertPageScope(parseDevicePage(value), projectId, "设备列表"),
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
            assertProjectScope(parseDevice(value), projectId, "设备"),
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
          assertPageScope(parseSensorPage(value), projectId, "传感器列表"),
      });
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
            parseCalibrationPage(value),
            projectId,
            "标定记录列表",
          ),
      });
    },
  };
}

export const devicesApi = createDevicesApi(apiClient);
