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
import { ContractValidationError } from "./validation";

/** 契约 §4.1：单页上限 100；设备详情页骨架一次加载一个上限页。 */
const MAX_PAGE_LIMIT = 100;

export interface PageRequestOptions {
  signal?: AbortSignal;
  cursor?: string;
  limit?: number;
}

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

function requireIdentifier(value: string, field: string): string {
  const trimmed = value.trim();
  if (trimmed === "") {
    throw new ContractValidationError(`${field} 不能为空`);
  }
  return encodeURIComponent(trimmed);
}

function projectPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${requireIdentifier(projectId, "project_id")}${suffix}`;
}

function pageQuery(options: PageRequestOptions | undefined): {
  cursor?: string;
  limit: number;
} {
  return {
    cursor: options?.cursor,
    limit: options?.limit ?? MAX_PAGE_LIMIT,
  };
}

function assertProjectScope<T extends { project_id: string }>(
  item: T,
  projectId: string,
  path: string,
): T {
  if (item.project_id !== projectId) {
    throw new ContractValidationError(`${path}.project_id 与请求路径不一致`);
  }
  return item;
}

function assertPageScope<T extends { project_id: string }>(
  page: CursorPage<T>,
  projectId: string,
  path: string,
): CursorPage<T> {
  page.items.forEach((item, index) => {
    assertProjectScope(item, projectId, `${path}.items[${index}]`);
  });
  return page;
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
