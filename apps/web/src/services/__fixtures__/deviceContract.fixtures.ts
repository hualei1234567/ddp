/**
 * 设备域契约 fixture（DDP-213）。
 *
 * 来源：真实 FastAPI 路由响应快照（真实 SQLite + 真实身份仓储 + 真实 DTO
 * 序列化），不是手写样例。快照文件：`deviceContract.snapshot.json`。
 *
 * 生成/校验命令（在 apps/web 下执行）：
 * - `python3 scripts/device_contract_snapshot.py --write`
 * - `npm run contract:check`
 *
 * 快照中的 UUID、标定配置与证据 ID 都是固定合成值，保证可重复生成；
 * 数据等级 T2，仅用于契约测试，不是业务验收数据。
 */
import type {
  CalibrationRecord,
  CursorPage,
  Device,
  Sensor,
} from "../../types/device";
import snapshot from "./deviceContract.snapshot.json";

export const backendDeviceFixture: Device = snapshot.device;
export const backendSensorFixture: Sensor = snapshot.sensor;
export const backendCalibrationFixture: CalibrationRecord = snapshot.calibration;
export const backendSensorPageFixture: CursorPage<Sensor> = snapshot.sensor_page;
export const backendCalibrationPageFixture: CursorPage<CalibrationRecord> =
  snapshot.calibration_page;
