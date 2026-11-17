import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./api";
import {
  backendCalibrationFixture,
  backendDeviceFixture,
  backendRobotFixture,
  backendSensorFixture,
} from "./__fixtures__/deviceContract.fixtures";
import { createDevicesApi, parseJsonObjectText } from "./devices";

const projectId = "11111111-1111-7111-8111-111111111111";
const deviceId = "22222222-2222-7222-8222-222222222222";

const device = backendDeviceFixture;
const sensor = backendSensorFixture;
const calibration = backendCalibrationFixture;
const robot = backendRobotFixture;

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

  it("加载更多时携带 next_cursor 与单页上限", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () =>
      jsonResponse({ items: [device], next_cursor: null }),
    );
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.listDevices(projectId, { cursor: "cursor-2", limit: 100 });

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices?cursor=cursor-2&limit=100`,
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

  it("创建设备走 POST /devices 并带幂等键", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse(device, { status: 201 }));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.createDevice(
      projectId,
      { code: "DEV-NEW", name: "新设备", device_type: "OTHER" },
      { idempotencyKey: "idem-device-create" },
    );

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices`,
    );
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "idem-device-create",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      code: "DEV-NEW",
      name: "新设备",
      device_type: "OTHER",
    });
  });

  it("编辑设备走 PATCH 且只带 If-Match", async () => {
    const fetchImpl = vi.fn<typeof fetch>(async () => jsonResponse(device));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.updateDevice(projectId, deviceId, { name: "改名" }, { version: 4 });

    const init = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices/${deviceId}`,
    );
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ "If-Match": '"4"' });
    expect(init?.headers).not.toHaveProperty("Idempotency-Key");
    expect(JSON.parse(String(init?.body))).toEqual({ name: "改名" });
  });

  it("设备生命周期动作映射到 /maintenance 与 /fault 路径并透传必填字段", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(device))
      .mockResolvedValueOnce(jsonResponse(device));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.actOnDevice(
      projectId,
      deviceId,
      "enter-maintenance",
      { reason: "例行保养", maintenance_record_id: "wf-1" },
      { version: 5, idempotencyKey: "idem-maint" },
    );
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices/${deviceId}/maintenance`,
    );
    const maintInit = fetchImpl.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(maintInit?.headers).toMatchObject({
      "If-Match": '"5"',
      "Idempotency-Key": "idem-maint",
    });
    expect(JSON.parse(String(maintInit?.body))).toEqual({
      reason: "例行保养",
      maintenance_record_id: "wf-1",
    });

    await api.actOnDevice(
      projectId,
      deviceId,
      "report-fault",
      { fault_code: "MOTOR_OVERTEMP" },
      { version: 6 },
    );
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/devices/${deviceId}/fault`,
    );
    expect(
      JSON.parse(String((fetchImpl.mock.calls[1]?.[1] as RequestInit).body)),
    ).toEqual({ fault_code: "MOTOR_OVERTEMP" });
  });

  it("设备响应中的 allowed_actions 被保留，缺失时不伪造", async () => {
    const withActions = { ...device, allowed_actions: ["activate", "suspend"] };
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(withActions))
      .mockResolvedValueOnce(jsonResponse({ ...withActions, allowed_actions: undefined }));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await expect(api.getDevice(projectId, deviceId)).resolves.toMatchObject({
      allowed_actions: ["activate", "suspend"],
    });
    await expect(api.getDevice(projectId, deviceId)).resolves.not.toHaveProperty(
      "allowed_actions",
    );
  });

  it("传感器创建与动作使用真实路径", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(sensor, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse(sensor));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.createSensor(projectId, {
      code: "CAM-1",
      sensor_type: "RGB",
      sample_rate_hz: 30,
      coordinate_frame: "camera_front_link",
      frame_id: "camera_front_link",
      device_id: device.device_id,
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/sensors`,
    );

    await api.actOnSensor(
      projectId,
      sensor.sensor_id,
      "activate",
      { reason: "到货验收" },
      { version: 2 },
    );
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/sensors/${sensor.sensor_id}/activate`,
    );
  });

  it("标定创建与状态动作：创建带 If-Match 之外的字段，动作只带 If-Match", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(calibration, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse(calibration));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.createCalibration(projectId, {
      profile_id: calibration.profile_id,
      target_type: "DEVICE",
      target_id: calibration.target_id,
      calibration_type: "INTRINSICS",
      method: "charuco",
      parameters: {},
      valid_from: "2026-11-08T00:00:00Z",
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/calibrations`,
    );

    await api.actOnCalibration(
      projectId,
      calibration.calibration_id,
      "validate",
      { version: calibration.version },
    );
    const init = fetchImpl.mock.calls[1]?.[1] as RequestInit | undefined;
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/calibrations/${calibration.calibration_id}/validate`,
    );
    expect(init?.headers).toMatchObject({ "If-Match": `"${calibration.version}"` });
    expect(init?.headers).not.toHaveProperty("Idempotency-Key");
  });

  it("机器人列表、详情、创建与动作走 /robots 路径", async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [robot], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse(robot))
      .mockResolvedValueOnce(jsonResponse(robot, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse(robot));
    const api = createDevicesApi(
      new ApiClient({
        baseUrl: "http://api.test",
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    );

    await api.listRobots(projectId, { status: "ACTIVE" });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/robots?limit=100&status=ACTIVE`,
    );

    await api.getRobot(projectId, robot.robot_id);
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/robots/${robot.robot_id}`,
    );

    await api.createRobot(projectId, {
      code: "ROBOT-2",
      name: "二号机器人",
      robot_type: "HUMANOID",
    });
    expect(fetchImpl.mock.calls[2]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/robots`,
    );

    await api.actOnRobot(
      projectId,
      robot.robot_id,
      "retire",
      { reason: "设备报废" },
      { version: 2 },
    );
    expect(fetchImpl.mock.calls[3]?.[0]).toBe(
      `http://api.test/api/v1/projects/${projectId}/robots/${robot.robot_id}/retire`,
    );
  });

  it("表单 JSON 对象只接受对象，空文本返回 null", () => {
    expect(parseJsonObjectText("  ", "参数")).toBeNull();
    expect(parseJsonObjectText('{"a":1}', "参数")).toEqual({ a: 1 });
    expect(() => parseJsonObjectText("[]", "参数")).toThrow(/必须是 JSON 对象/);
    expect(() => parseJsonObjectText("{", "参数")).toThrow(/不是合法的 JSON/);
  });
});
