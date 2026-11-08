import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./api";
import { createDevicesApi } from "./devices";

const projectId = "11111111-1111-7111-8111-111111111111";
const deviceId = "22222222-2222-7222-8222-222222222222";

const device = {
  device_id: deviceId,
  project_id: projectId,
  robot_id: null,
  code: "DEV-001",
  name: "主控计算单元",
  device_type: "COMPUTE",
  vendor: "Acme",
  model: "X1",
  serial_number: "SN-001",
  firmware_version: null,
  hardware_version: null,
  calibration_profile_id: null,
  capability_profile: null,
  status: "ACTIVE",
  location_id: null,
  owner_user_id: null,
  version: 1,
  created_at: null,
  updated_at: null,
  created_by: null,
  updated_by: null,
};

const sensor = {
  sensor_id: "sensor-1",
  project_id: projectId,
  robot_id: null,
  device_id: deviceId,
  code: "CAM-001",
  sensor_type: "RGB",
  vendor: null,
  model: null,
  serial_number: null,
  sample_rate_hz: 30,
  resolution_w: 1920,
  resolution_h: 1080,
  bit_depth: 8,
  channels: null,
  intrinsic_calibration_id: null,
  extrinsic_calibration_id: null,
  coordinate_frame: null,
  sync_mode: null,
  sync_group: null,
  firmware_version: null,
  calibration_profile_id: null,
  status: "ACTIVE",
  mount_position: null,
  version: 1,
  created_at: null,
  updated_at: null,
};

const calibration = {
  calibration_id: "cal-1",
  project_id: projectId,
  target_type: "DEVICE",
  target_id: deviceId,
  calibration_type: "EXTRINSIC",
  status: "ACTIVE",
  evidence_asset_ids: [],
  version: 1,
  created_at: null,
  updated_at: null,
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8" },
    ...init,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("设备域 API", () => {
  it("按契约路径与游标参数请求设备列表", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [device], next_cursor: null }),
    );
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await expect(api.listDevices(projectId)).resolves.toMatchObject({
      items: [device],
      next_cursor: null,
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices?limit=100`,
    );
  });

  it("详情路径对 ID 做 URL 编码", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse(device));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.getDevice(projectId, "dev/1");

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices/dev%2F1`,
    );
  });

  it("传感器与标定查询携带设备过滤条件", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [sensor], next_cursor: null }))
      .mockResolvedValueOnce(
        jsonResponse({ items: [calibration], next_cursor: null }),
      );
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.listSensors(projectId, deviceId);
    await api.listCalibrations(projectId, deviceId);

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/sensors?device_id=${deviceId}&limit=100`,
    );
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/calibrations?limit=100&target_id=${deviceId}&target_type=DEVICE`,
    );
  });

  it("响应中的 project_id 与路径不一致时视为契约异常", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        items: [{ ...device, project_id: "other-project" }],
        next_cursor: null,
      }),
    );
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await expect(api.listDevices(projectId)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("403 错误体保持 forbidden 语义", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse(
        {
          error: {
            code: "project_scope_denied",
            message: "无权访问该项目",
            request_id: "rid-1",
          },
        },
        { status: 403 },
      ),
    );
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    const error = await api
      .listDevices(projectId)
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      code: "project_scope_denied",
    });
    expect((error as ApiError).isForbidden).toBe(true);
  });
});
